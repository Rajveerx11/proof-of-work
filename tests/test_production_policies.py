"""Production coverage policy, captured trust inputs and signed evidence.

All commits/branch changes below belong to ephemeral fixture repositories, never
this checkout. Real collector tests deliberately do not synthesize base_commit.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from proofofwork import engine, log
from proofofwork.core import detector, gitdiff, runner
from proofofwork.core.detector.coverage_delta import METRIC
from proofofwork.core.sandbox import RunOutput
from proofofwork.types import Severity, TestResult


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def payload(coverage=90, languages=None):
    return {"coverage": coverage, "metric": METRIC,
            "languages": ["python"] if languages is None else languages}


def repo(root, baseline=None):
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "fixture")
    (root / ".gitignore").write_text(".proofofwork/\n")
    (root / "app.py").write_text("value = 1\n")
    (root / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value > 0\n")
    git(root, "add", "-A")
    if baseline is not None:
        put(root / ".proofofwork/baseline.json", baseline)
        git(root, "add", "-f", ".proofofwork/baseline.json")
    git(root, "commit", "-qm", "reviewed base")
    commit = git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("value = 2\n")
    git(root, "add", "app.py")
    return commit


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))


def result(coverage=100, **kwargs):
    return TestResult(ran=True, passed=True, coverage=coverage, framework="pytest", **kwargs)


def rules(verdict):
    return {f.rule for f in verdict.findings if f.severity == Severity.BLOCK}


def test_real_collector_supplies_immutable_policy_commit(tmp_path):
    base = repo(tmp_path)
    assert gitdiff.collect_diff(str(tmp_path)).base_commit == base
    assert gitdiff.collect_diff(str(tmp_path), staged=True).base_commit == base


@pytest.mark.parametrize("staged", [False, True])
def test_candidate_only_baseline_is_not_a_trusted_floor(monkeypatch, tmp_path, staged):
    base = repo(tmp_path)
    put(tmp_path / ".proofofwork/baseline.json", payload(0))
    git(tmp_path, "add", "-f", ".proofofwork/baseline.json")
    if not staged:
        git(tmp_path, "commit", "-qm", "candidate proposes its own floor")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: result())
    verdict = engine.check(str(tmp_path), base_ref=base if not staged else "HEAD", staged=staged)
    assert not verdict.passed, verdict.reasons
    assert verdict.coverage_baseline is None
    assert "coverage-baseline-missing" in rules(verdict)


@pytest.mark.parametrize("phase", ["detector", "runner"])
@pytest.mark.parametrize("staged", [False, True])
def test_trusted_local_floor_and_identity_are_captured_before_execution(monkeypatch, tmp_path, phase, staged):
    repo(tmp_path)
    path = tmp_path / (".git/proofofwork/baseline.json" if staged else ".proofofwork/baseline.json")
    put(path, payload(100))

    def tamper():
        put(path, payload(0, ["js"]))

    def detector_call(diff, root):
        if phase == "detector":
            tamper()
        return []

    def run(*a, **k):
        if phase == "runner":
            tamper()
        return result(80)

    monkeypatch.setattr(detector, "ALL_CHECKS", [detector_call])
    monkeypatch.setattr(runner, "run_tests", run)
    verdict = engine.check(str(tmp_path), staged=staged)
    assert not verdict.passed
    assert verdict.coverage_baseline == 100
    assert "coverage-drop" in rules(verdict)
    assert "coverage-incomparable" not in rules(verdict)


def test_explicit_branch_floor_is_pinned_before_detector_moves_ref(monkeypatch, tmp_path):
    base = repo(tmp_path, payload(100))
    git(tmp_path, "branch", "reviewed", base)
    put(tmp_path / ".proofofwork/baseline.json", payload(0))
    git(tmp_path, "add", "-f", ".proofofwork/baseline.json")
    git(tmp_path, "commit", "-qm", "candidate low floor")
    candidate = git(tmp_path, "rev-parse", "HEAD")

    def move_ref(diff, root):
        git(tmp_path, "update-ref", "refs/heads/reviewed", candidate)
        return []

    monkeypatch.setattr(detector, "ALL_CHECKS", [move_ref])
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: result(80))
    verdict = engine.check(str(tmp_path), base_ref="reviewed")
    assert not verdict.passed
    assert verdict.coverage_baseline == 100
    assert "coverage-drop" in rules(verdict)


@pytest.mark.parametrize("location", ["ordinary-local", "staged-local", "explicit-commit", "staged-commit"])
@pytest.mark.parametrize("baseline", [payload(), {"coverage": 90}, "malformed"])
def test_test_only_cannot_bypass_any_existing_trusted_baseline(monkeypatch, tmp_path, location, baseline):
    committed = location.endswith("commit")
    base = repo(tmp_path, baseline if committed else None)
    staged = location.startswith("staged")
    if not committed:
        put(tmp_path / (".git/proofofwork/baseline.json" if staged else ".proofofwork/baseline.json"), baseline)
    calls = []
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: calls.append(1) or result())
    verdict = engine.check(str(tmp_path), base_ref=base if location == "explicit-commit" else "HEAD",
                           staged=staged, coverage_policy="test-only")
    assert not verdict.passed
    assert "coverage-policy-bypass" in rules(verdict)
    assert calls == []
    assert verdict.coverage_status == "not-verified"


@pytest.mark.parametrize("baseline", [
    {"coverage": 90},
    {"coverage": 90, "metric": "branch-v1", "languages": ["python"]},
    payload(90, ["js"]),
])
@pytest.mark.parametrize("bootstrap", [False, True])
def test_required_legacy_or_different_identity_requires_reviewed_migration(monkeypatch, tmp_path, baseline, bootstrap):
    repo(tmp_path)
    path = tmp_path / ".proofofwork/baseline.json"
    put(path, baseline)
    before = path.read_bytes()
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: result())
    verdict = engine.check(str(tmp_path), update_baseline=bootstrap)
    assert not verdict.passed and "coverage-incomparable" in rules(verdict)
    assert "migrate" in " ".join(verdict.reasons)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["test-only", "no-tests", "required-bootstrap"])
def test_policy_and_execution_labels_are_serialized_and_durably_signed(monkeypatch, tmp_path, mode):
    repo(tmp_path)
    captured = []
    original = log.record

    def record(envelope, db):
        captured.append(envelope)
        return original(envelope, db)

    def run(*a, **k):
        assert k.get("collect_coverage", True) is (mode != "test-only")
        assert mode != "no-tests"
        return result()  # Engine also discards unexpected opt-out measurements.

    monkeypatch.setattr(log, "record", record)
    monkeypatch.setattr(runner, "run_tests", run)
    options = ({"coverage_policy": "test-only"} if mode == "test-only" else
               {"run_tests": False} if mode == "no-tests" else {"update_baseline": True})
    verdict = engine.check(str(tmp_path), **options)
    assert verdict.passed, verdict.reasons
    assert verdict.entry_hash and log.verify_chain(str(tmp_path / ".proofofwork/log.db"))
    expected = {"coverage_policy": "test-only" if mode == "test-only" else "required",
                "integrity_policy": "strict", "execution_mode": "detector-only" if mode == "no-tests" else "trusted-local",
                "coverage_status": "verified" if mode == "required-bootstrap" else
                                   "not-run" if mode == "no-tests" else "not-verified"}
    for name, value in expected.items():
        assert verdict.as_dict()[name] == value
        assert captured[0]["predicate"][name] == value
    if mode != "required-bootstrap":
        assert captured[0]["predicate"]["coverage"] is None
        assert captured[0]["predicate"]["js_coverage"] is None
    if mode == "no-tests":
        assert captured[0]["predicate"]["tests_passed"] is None
        assert "detector-only" in " ".join(verdict.reasons)
    if mode == "test-only":
        assert "NOT verified" in " ".join(verdict.reasons)


@pytest.mark.parametrize("missing", ["tool", "report"])
def test_required_missing_instrumentation_or_fresh_report_blocks_real_git_check(monkeypatch, tmp_path, missing):
    repo(tmp_path, payload(90))
    (tmp_path / "coverage.json").write_text('{"totals":{"percent_covered":100}}')

    class Model:
        def run(self, cmd, **kwargs):
            if "import coverage" in cmd and missing == "tool":
                return RunOutput(code=1, stdout="", stderr="coverage unavailable")
            if "-q" in cmd:
                Path(kwargs["env"]["PROOFOFWORK_PYTEST_WITNESS"]).write_text('{"executed":1}')
            return RunOutput(code=0, stdout="", stderr="")

    monkeypatch.setattr("proofofwork.core.sandbox.get_sandbox", lambda _: Model())
    verdict = engine.check(str(tmp_path))
    assert verdict.tests.ran and verdict.tests.passed
    assert not verdict.passed and "coverage-unavailable" in rules(verdict)
    assert verdict.coverage_status == "not-verified"


@pytest.mark.parametrize("python,js,passed,ran", [
    (90, None, True, True), (None, 90, True, True), (90, float("nan"), True, True),
    (90, 80, False, True), (90, 80, True, False),
])
def test_bootstrap_never_suppresses_missing_or_invalid_metrics_or_nonpassing_execution(monkeypatch, tmp_path, python, js, passed, ran):
    repo(tmp_path)
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=ran, passed=passed, coverage=python, js_coverage=js, framework="pytest+vitest"))
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert not verdict.passed
    assert not (tmp_path / ".proofofwork/baseline.json").exists()
    assert verdict.coverage_status == "not-verified"
    if not passed or not ran:
        assert "coverage-baseline-missing" in rules(verdict)


def test_mixed_bootstrap_records_minimum_only_with_complete_passing_metrics(monkeypatch, tmp_path):
    repo(tmp_path)
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=90, js_coverage=80, framework="pytest+vitest"))
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert verdict.passed, verdict.reasons
    assert json.loads((tmp_path / ".proofofwork/baseline.json").read_text()) == payload(80, ["js", "python"])
    assert verdict.coverage_baseline == 80 and verdict.coverage_status == "verified"


def test_bootstrap_rollback_preserves_concurrently_changed_identity(monkeypatch, tmp_path):
    repo(tmp_path)
    path = tmp_path / ".proofofwork/baseline.json"
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: result(80))

    def fail_log(*args):
        put(path, payload(80, ["js"]))
        raise OSError("log unavailable")

    monkeypatch.setattr(log, "record", fail_log)
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert not verdict.passed and verdict.coverage_status == "not-verified"
    assert json.loads(path.read_text()) == payload(80, ["js"])


def test_actual_required_python_gate_positive_and_negative(tmp_path):
    repo(tmp_path, payload(100))
    positive = engine.check(str(tmp_path))
    assert positive.passed and positive.coverage_status == "verified", positive.reasons
    (tmp_path / "app.py").write_text("value = 2\n\ndef never_called():\n" +
                                   "".join(f"    x{i} = {i}\n" for i in range(30)))
    negative = engine.check(str(tmp_path))
    assert negative.tests.passed and not negative.passed, negative.reasons
    assert "coverage-drop" in rules(negative)


@pytest.mark.parametrize("staged", [False, True])
def test_test_only_existence_is_captured_before_detector_removes_local_floor(monkeypatch, tmp_path, staged):
    repo(tmp_path)
    path = tmp_path / (".git/proofofwork/baseline.json" if staged else ".proofofwork/baseline.json")
    put(path, payload(90))
    calls = []

    def remove_floor(diff, root):
        path.unlink()
        return []

    monkeypatch.setattr(detector, "ALL_CHECKS", [remove_floor])
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: calls.append(1) or result())
    verdict = engine.check(str(tmp_path), staged=staged, coverage_policy="test-only")
    assert not verdict.passed and "coverage-policy-bypass" in rules(verdict)
    assert calls == []


def test_deleted_committed_floor_cannot_enable_ordinary_head_test_only(monkeypatch, tmp_path):
    repo(tmp_path, payload(90))
    (tmp_path / ".proofofwork/baseline.json").unlink()
    calls = []
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: calls.append(1) or result())
    verdict = engine.check(str(tmp_path), coverage_policy="test-only")
    assert not verdict.passed and "coverage-policy-bypass" in rules(verdict)
    assert calls == []


def configured_repo(root):
    repo(root)
    put(root / ".proofofwork/suites.json", {"version": 1, "suites": [{
        "id": "native", "cwd": ".", "argv": [Path(sys.executable).name, "-c", "print('suite executed')"],
        "prerequisites": [], "timeout": 10, "evidence": "exit-code"}]})
    git(root, "add", "-f", ".proofofwork/suites.json")
    git(root, "commit", "-qm", "reviewed exit evidence")


@pytest.mark.parametrize("staged", [False, True])
def test_configured_local_floor_restriction_is_captured_before_detectors(monkeypatch, tmp_path, staged):
    configured_repo(tmp_path)
    path = tmp_path / (".git/proofofwork/baseline.json" if staged else ".proofofwork/baseline.json")
    put(path, payload())
    calls = []

    def remove_floor(diff, root):
        path.unlink()
        return []

    monkeypatch.setattr(detector, "ALL_CHECKS", [remove_floor])
    monkeypatch.setattr(engine, "_run_configured_tests", lambda *a, **k: calls.append(1) or result())
    verdict = engine.check(str(tmp_path), staged=staged, suite_base="HEAD", coverage_policy="test-only")
    assert not verdict.passed
    assert {"suite-config-error", "coverage-policy-bypass"} <= rules(verdict)
    assert calls == []


@pytest.mark.parametrize("policy", ["required", "test-only"])
def test_real_configured_exit_evidence_requires_explicit_test_only(tmp_path, policy):
    configured_repo(tmp_path)
    verdict = engine.check(str(tmp_path), suite_base="HEAD", coverage_policy=policy)
    if policy == "required":
        assert not verdict.passed and "suite-config-error" in rules(verdict)
        assert verdict.tests.executed_suites == []
        assert "explicit coverage_policy='test-only'" in " ".join(verdict.reasons)
    else:
        assert verdict.passed, verdict.reasons
        assert verdict.tests.executed_suites == ["native"]
        assert verdict.tests.evidence == "exit-code"
    assert verdict.coverage_status == "not-verified"
    assert verdict.tests.coverage is None and verdict.tests.js_coverage is None


def test_invalid_policy_cannot_execute_or_claim_verified_coverage(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError("execution must not start")

    monkeypatch.setattr(runner, "run_tests", forbidden)
    verdict = engine.check(str(tmp_path), coverage_policy="optional")
    assert not verdict.passed and "coverage-policy-invalid" in rules(verdict)
    assert verdict.coverage_status == "not-verified" and not verdict.entry_hash
