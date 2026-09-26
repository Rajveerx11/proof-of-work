"""Fail-closed behavior at the production gate boundaries."""
from __future__ import annotations

import json
import subprocess

from proofofwork import engine
from proofofwork.core import detector, runner
from proofofwork.types import Diff, Finding, Severity, TestResult


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def test_bad_ref_returns_failure_not_empty_pass(tmp_path):
    _git(tmp_path, "init", "-q")
    verdict = engine.check(str(tmp_path), "missing-ref", run_tests=False)
    assert not verdict.passed
    assert verdict.findings[0].rule == "git-diff"
    assert not verdict.entry_hash


def test_full_check_requires_successful_test_run(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_changeset_sha", lambda diff: "subject")
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult())
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed
    assert any("tests-unavailable" in reason for reason in verdict.reasons)
    detector_only = engine.check(str(tmp_path), run_tests=False)
    assert detector_only.passed
    assert "detector-only" in " ".join(detector_only.reasons)


def test_broken_builtin_detector_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())

    def broken(diff, root):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(detector, "ALL_CHECKS", [broken])
    verdict = engine.check(str(tmp_path), run_tests=False)
    assert not verdict.passed
    assert any(f.rule == "check-error:broken" and f.severity == Severity.BLOCK
               for f in verdict.findings)


def test_log_failure_cannot_produce_signed_pass(monkeypatch, tmp_path):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr("proofofwork.log.record", broken)
    verdict = engine.check(str(tmp_path), run_tests=False)
    assert not verdict.passed
    assert verdict.entry_hash == ""
    assert any("log-unavailable" in reason for reason in verdict.reasons)


def test_baseline_bootstrap_requires_passing_measured_tests(monkeypatch, tmp_path):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    for result in (TestResult(), TestResult(ran=True, passed=False, coverage=77)):
        monkeypatch.setattr(runner, "run_tests", lambda *a, result=result: result)
        verdict = engine.check(str(tmp_path), update_baseline=True)
        assert not verdict.passed
        assert not baseline.exists()
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=77))
    assert not engine.check(str(tmp_path)).passed  # no implicit bootstrap
    assert engine.check(str(tmp_path), update_baseline=True).passed
    assert json.loads(baseline.read_text())["coverage"] == 77
    assert engine.check(str(tmp_path)).passed
    assert not engine.check(str(tmp_path), run_tests=False, update_baseline=True).passed


def test_staged_runs_index_not_worktree_and_does_not_mutate_either(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    test = tmp_path / "test_snapshot.py"
    test.write_text("def test_value():\n    assert True\n")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 0}')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    test.write_text("def test_value():\n    assert 2 == 2\n")
    _git(tmp_path, "add", "test_snapshot.py")
    test.write_text("def test_value():\n    assert False\n")
    index_before = _git(tmp_path, "ls-files", "--stage")
    status_before = _git(tmp_path, "status", "--porcelain")
    work_before = test.read_bytes()
    verdict = engine.check(str(tmp_path), staged=True)
    assert verdict.passed, verdict.reasons
    assert verdict.tests.passed is True
    assert test.read_bytes() == work_before
    assert _git(tmp_path, "ls-files", "--stage") == index_before
    assert _git(tmp_path, "status", "--porcelain") == status_before
    assert (tmp_path / ".git" / "proofofwork" / "log.db").exists()
    assert not engine.check(str(tmp_path)).passed
    assert not engine.check(str(tmp_path), staged=True, update_baseline=True).passed
    assert baseline.read_text() == '{"coverage": 0}'


def test_staged_baseline_uses_committed_value_not_staged_edit(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 0}')
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    baseline.write_text('{"coverage": 100}')
    (tmp_path / "app.py").write_text("x = 2\n")
    _git(tmp_path, "add", "-A")
    before = _git(tmp_path, "ls-files", "--stage")
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=80))
    verdict = engine.check(str(tmp_path), staged=True)
    assert verdict.passed, verdict.reasons
    assert verdict.coverage_baseline == 0
    assert baseline.read_text() == '{"coverage": 100}'
    assert _git(tmp_path, "ls-files", "--stage") == before


def test_extra_block_still_enforced_in_detector_only_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    verdict = engine.check(str(tmp_path), run_tests=False,
                           extra_findings=[Finding("protected", Severity.BLOCK, "modified")])
    assert not verdict.passed
