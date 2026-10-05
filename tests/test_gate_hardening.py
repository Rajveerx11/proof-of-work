"""Fail-closed behavior at the production gate boundaries."""
from __future__ import annotations

import importlib
import json
import math
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from proofofwork import engine
from proofofwork.core import detector, runner
from proofofwork.types import Diff, Finding, Severity, TestResult


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout



def _init_empty(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "commit", "--allow-empty", "-qm", "base")


def _commit_baseline(root):
    _git(root, "add", "-f", ".proofofwork/baseline.json")
    _git(root, "commit", "-qm", "reviewed fixture baseline")


def test_option_looking_base_ref_cannot_suppress_diff(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "start")
    (tmp_path / "app.py").write_text("value = 2\n")
    verdict = engine.check(str(tmp_path), "--quiet", run_tests=False)
    assert not verdict.passed
    assert verdict.findings[0].rule == "git-diff"


def test_bad_ref_returns_failure_not_empty_pass(tmp_path):
    _git(tmp_path, "init", "-q")
    verdict = engine.check(str(tmp_path), "missing-ref", run_tests=False)
    assert not verdict.passed
    assert verdict.findings[0].rule == "git-diff"
    assert not verdict.entry_hash


def test_full_check_requires_successful_test_run(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_changeset_sha", lambda diff: "subject")
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult())
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed
    assert any("tests-unavailable" in reason for reason in verdict.reasons)
    detector_only = engine.check(str(tmp_path), run_tests=False)
    assert detector_only.passed
    assert "detector-only" in " ".join(detector_only.reasons)


def test_broken_builtin_detector_blocks(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())

    def broken(diff, root):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(detector, "ALL_CHECKS", [broken])
    verdict = engine.check(str(tmp_path), run_tests=False)
    assert not verdict.passed
    assert any(f.rule == "check-error:broken" and f.severity == Severity.BLOCK
               for f in verdict.findings)


def test_log_failure_cannot_produce_signed_pass(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr("proofofwork.log.record", broken)
    verdict = engine.check(str(tmp_path), run_tests=False)
    assert not verdict.passed
    assert verdict.entry_hash == ""
    assert any("log-unavailable" in reason for reason in verdict.reasons)


def test_baseline_bootstrap_requires_passing_measured_tests(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    for result in (TestResult(), TestResult(ran=True, passed=False, coverage=77, framework="pytest"),
                   TestResult(ran=True, passed=True, coverage=float("nan"), framework="pytest"),
                   TestResult(ran=True, passed=True, coverage=101, framework="pytest")):
        monkeypatch.setattr(runner, "run_tests", lambda *a, result=result: result)
        verdict = engine.check(str(tmp_path), update_baseline=True)
        assert not verdict.passed
        assert not baseline.exists()
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=77, framework="pytest"))
    assert not engine.check(str(tmp_path)).passed  # no implicit bootstrap
    assert engine.check(str(tmp_path), update_baseline=True).passed
    assert json.loads(baseline.read_text())["coverage"] == 77
    _commit_baseline(tmp_path)
    assert engine.check(str(tmp_path)).passed
    assert not engine.check(str(tmp_path), run_tests=False, update_baseline=True).passed
    assert not engine.check(str(tmp_path), update_baseline=True).passed
    assert json.loads(baseline.read_text())["coverage"] == 77


def test_bootstrap_does_not_write_on_other_block_or_existing_bad_baseline(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=80, framework="pytest"))
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    verdict = engine.check(str(tmp_path), update_baseline=True,
                           extra_findings=[Finding("other", Severity.BLOCK, "bad")])
    assert not verdict.passed and not baseline.exists()
    baseline.parent.mkdir(exist_ok=True)
    baseline.write_text('{"coverage": 90, "metric": "project-lines-v1", "languages": ["python"]}')
    _commit_baseline(tmp_path)
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert not verdict.passed and baseline.read_text() == '{"coverage": 90, "metric": "project-lines-v1", "languages": ["python"]}'
    assert any(f.rule == "coverage-drop" for f in verdict.findings)
    assert not engine.check(str(tmp_path)).passed  # measured coverage dropped
    baseline.write_text("invalid")
    _commit_baseline(tmp_path)
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert not verdict.passed and baseline.read_text() == "invalid"


def test_mixed_coverage_checks_both_suites_against_scalar_baseline(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 90, "metric": "project-lines-v1", "languages": ["js", "python"]}')
    _commit_baseline(tmp_path)
    for python_coverage, js_coverage, expected in (
        (90, 10, "coverage-drop"),
        (70, 95, "coverage-drop"),
        (90, None, "coverage-unavailable"),
        (None, 95, "coverage-unavailable"),
        (90, float("nan"), "coverage-invalid"),
    ):
        result = TestResult(ran=True, passed=True, coverage=python_coverage,
                            js_coverage=js_coverage, framework="pytest+vitest")
        monkeypatch.setattr(runner, "run_tests", lambda *a, result=result: result)
        verdict = engine.check(str(tmp_path))
        assert not verdict.passed
        assert any(f.rule == expected for f in verdict.findings)
        assert verdict.tests.coverage == python_coverage
        if js_coverage is not None and math.isnan(js_coverage):
            assert math.isnan(verdict.tests.js_coverage)
        else:
            assert verdict.tests.js_coverage == js_coverage
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(
        ran=True, passed=True, coverage=90, js_coverage=95, framework="pytest+vitest"))
    assert engine.check(str(tmp_path)).passed
    baseline.unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "-qm", "explicit fixture reset")
    assert engine.check(str(tmp_path), update_baseline=True).passed
    assert json.loads(baseline.read_text()) == {"coverage": 90, "metric": "project-lines-v1", "languages": ["js", "python"]}


def test_mixed_js_coverage_requires_missing_baseline_bootstrap(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(
        ran=True, passed=True, coverage=None, js_coverage=75, framework="pytest+vitest"))
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed
    assert any(f.rule == "coverage-baseline-missing" and f.severity == Severity.BLOCK
               for f in verdict.findings)
    assert not engine.check(str(tmp_path), update_baseline=True).passed
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(
        ran=True, passed=True, coverage=80, js_coverage=75, framework="pytest+vitest"))
    verdict = engine.check(str(tmp_path))
    assert sum(f.rule == "coverage-baseline-missing" for f in verdict.findings) == 1


def test_learned_exception_blocks(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())

    def broken(*args):
        raise RuntimeError("learned failed")

    monkeypatch.setattr("proofofwork.core.detector.learned.check", broken)
    verdict = engine.check(str(tmp_path), run_tests=False)
    assert not verdict.passed
    assert any(f.rule == "check-error:learned" and f.severity == Severity.BLOCK
               for f in verdict.findings)


def test_bootstrap_rolls_back_if_log_fails(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=80, framework="pytest"))

    def broken(*args):
        raise OSError("log unavailable")

    monkeypatch.setattr("proofofwork.log.record", broken)
    verdict = engine.check(str(tmp_path), update_baseline=True)
    assert not verdict.passed
    assert not (tmp_path / ".proofofwork" / "baseline.json").exists()


def test_staged_runs_index_not_worktree_and_does_not_mutate_either(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    test = tmp_path / "test_snapshot.py"
    test.write_text("def test_value():\n    assert True\n")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}')
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
    assert baseline.read_text() == '{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}'


def test_staged_python_src_ignores_unstaged_and_inherited_imports(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    src = tmp_path / "src"
    src.mkdir()
    (src / "feature.py").write_text("value = 1\n")
    test = tmp_path / "test_feature.py"
    test.write_text("from feature import value\n\ndef test_value():\n    assert value == 2\n")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (src / "feature.py").write_text("value = 2\n")
    _git(tmp_path, "add", "src/feature.py")
    (src / "feature.py").write_text("value = 3\n")
    monkeypatch.setenv("PYTHONPATH", str(src))
    verdict = engine.check(str(tmp_path), staged=True)
    assert verdict.passed, verdict.reasons
    assert verdict.tests.passed is True
    assert (src / "feature.py").read_text() == "value = 3\n"
    assert not engine.check(str(tmp_path)).passed


def test_staged_bootstrap_preserves_unrelated_editable_finder(monkeypatch, tmp_path):
    source = tmp_path / "project"
    src = source / "src"
    src.mkdir(parents=True)
    (src / "unstaged_feature.py").write_text("value = 'unstaged'\n")
    dependency = tmp_path / "dependency" / "editable_dependency_test.py"
    dependency.parent.mkdir()
    dependency.write_text("value = 'installed'\n")
    snapshot = tmp_path / "snapshot"
    env = engine._staged_python_env(str(source), str(snapshot), str(tmp_path))
    assert str(snapshot / "src") in env["PYTHONPATH"]

    def finder_for(name, path):
        module_name = f"__editable___{name}_finder"
        module = ModuleType(module_name)
        module.MAPPING = {name: str(path)}
        monkeypatch.setitem(sys.modules, module_name, module)

        def find_spec(self, fullname, path=None, target=None):
            if fullname == name:
                return importlib.util.spec_from_file_location(fullname, module.MAPPING[name])
            return None

        return type("EditableFinder", (), {"__module__": module_name,
                                            "find_spec": find_spec})()

    own_finder = finder_for("unstaged_feature", src / "unstaged_feature.py")
    dependency_finder = finder_for("editable_dependency_test", dependency)
    monkeypatch.setattr(sys, "path", [*sys.path, str(src)])
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, own_finder, dependency_finder])
    runpy.run_path(str(tmp_path / "python-bootstrap" / "sitecustomize.py"))
    assert str(src) not in sys.path
    assert own_finder not in sys.meta_path
    assert importlib.util.find_spec("unstaged_feature") is None
    assert dependency_finder in sys.meta_path
    assert importlib.import_module("editable_dependency_test").value == "installed"
    sys.modules.pop("editable_dependency_test", None)


def test_staged_bootstrap_removes_own_namespace_hook_and_placeholder(monkeypatch, tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    own = source / "src" / "own_namespace"
    own.mkdir(parents=True)
    dependency = tmp_path / "dependency" / "other_namespace"
    dependency.mkdir(parents=True)
    (own / "unstaged.py").write_text("value = 'unstaged'\n")
    bootstrap = engine._staged_python_env(str(source), str(tmp_path / "snapshot"), str(tmp_path))

    def install(name, path):
        module_name = f"__editable___{name}_finder"
        module = ModuleType(module_name)
        module.NAMESPACES = {name: [str(path)]}
        module.PATH_PLACEHOLDER = f"__editable__.{name}.__path_hook__"
        monkeypatch.setitem(sys.modules, module_name, module)

        def path_hook(path_entry):
            if path_entry != module.PATH_PLACEHOLDER:
                raise ImportError
            return importlib.machinery.FileFinder(str(path))

        path_hook.__module__ = module_name
        return module.PATH_PLACEHOLDER, path_hook

    own_placeholder, own_hook = install("own_namespace", own)
    dep_placeholder, dep_hook = install("other_namespace", dependency)
    monkeypatch.setattr(sys, "path", [*sys.path, own_placeholder, dep_placeholder])
    monkeypatch.setattr(sys, "path_hooks", [*sys.path_hooks, own_hook, dep_hook])
    monkeypatch.setattr(sys, "path_importer_cache", dict(sys.path_importer_cache))
    sys.path_importer_cache[own_placeholder] = object()
    sys.path_importer_cache[dep_placeholder] = object()
    runpy.run_path(str(tmp_path / "python-bootstrap" / "sitecustomize.py"))
    assert own_placeholder not in sys.path
    assert own_hook not in sys.path_hooks
    assert own_placeholder not in sys.path_importer_cache
    assert dep_placeholder in sys.path
    assert dep_hook in sys.path_hooks
    assert dep_placeholder in sys.path_importer_cache
    assert str(tmp_path / "snapshot" / "src") in bootstrap["PYTHONPATH"]


def test_staged_baseline_uses_committed_value_not_staged_edit(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}')
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    baseline.write_text('{"coverage": 100}')
    (tmp_path / "app.py").write_text("x = 2\n")
    _git(tmp_path, "add", "-A")
    before = _git(tmp_path, "ls-files", "--stage")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True,
                                                                          coverage=80, framework="pytest"))
    verdict = engine.check(str(tmp_path), staged=True)
    assert verdict.passed, verdict.reasons
    assert verdict.coverage_baseline == 0
    assert baseline.read_text() == '{"coverage": 100}'
    assert _git(tmp_path, "ls-files", "--stage") == before


def test_pr_cannot_lower_base_coverage_baseline(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 90, "metric": "project-lines-v1", "languages": ["python"]}')
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-f", ".proofofwork/baseline.json")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "trusted base")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()
    baseline.write_text('{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}')
    (tmp_path / "app.py").write_text("x = 2\n")
    _git(tmp_path, "add", "-f", ".proofofwork/baseline.json")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "lower coverage baseline")
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=70, framework="pytest"))
    verdict = engine.check(str(tmp_path), base_ref=base)
    assert not verdict.passed
    assert verdict.coverage_baseline == 90
    assert any(f.rule == "coverage-drop" for f in verdict.findings)
    assert not engine.check(str(tmp_path), base_ref=base, update_baseline=True).passed
    assert baseline.read_text() == '{"coverage": 0, "metric": "project-lines-v1", "languages": ["python"]}'


def test_first_pr_baseline_adoption_is_explicit(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "base without baseline")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text('{"coverage": 10}')
    _git(tmp_path, "add", "-f", ".proofofwork/baseline.json")
    _git(tmp_path, "commit", "-qm", "first proposed baseline")
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True,
                                                                     coverage=80, framework="pytest"))
    verdict = engine.check(str(tmp_path), base_ref=base)
    assert not verdict.passed
    assert verdict.coverage_baseline is None
    assert any(f.rule == "coverage-baseline-missing" for f in verdict.findings)


def test_staged_bootstrap_uses_git_metadata_only(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "start")
    (tmp_path / "app.py").write_text("value = 2\n")
    _git(tmp_path, "add", "-A")
    index_before = _git(tmp_path, "ls-files", "--stage")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True,
                                                                          coverage=65, framework="pytest"))
    verdict = engine.check(str(tmp_path), staged=True, update_baseline=True)
    assert verdict.passed, verdict.reasons
    assert not (tmp_path / ".proofofwork" / "baseline.json").exists()
    assert json.loads((tmp_path / ".git" / "proofofwork" / "baseline.json").read_text()) == {"coverage": 65, "metric": "project-lines-v1", "languages": ["python"]}
    assert _git(tmp_path, "ls-files", "--stage") == index_before
    assert not engine.check(str(tmp_path), staged=True).passed  # bootstrap must be reviewed/committed
    assert not engine.check(str(tmp_path), staged=True, update_baseline=True).passed


def test_staged_js_receives_ignored_dependencies_and_git_context(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    (tmp_path / "app.js").write_text("const answer = 1;\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "start")
    (tmp_path / "app.js").write_text("const answer = 2;\n")
    _git(tmp_path, "add", "-A")
    (tmp_path / "app.js").write_text("const answer = 3;\n")
    deps = tmp_path / "node_modules" / "example"
    deps.mkdir(parents=True)
    (deps / "index.js").write_text("module.exports = true;\n")

    def inspect(sandbox, snapshot, languages, *, collect_coverage=True):
        assert collect_coverage is False
        assert (tmp_path / "app.js").read_text() == "const answer = 3;\n"
        assert (Path(snapshot) / "app.js").read_text() == "const answer = 2;\n"
        assert (Path(snapshot) / "node_modules/example/index.js").is_file()
        assert _git(snapshot, "rev-parse", "HEAD").strip() == _git(tmp_path, "rev-parse", "HEAD").strip()
        assert "app.js" in _git(snapshot, "diff", "--name-only", "HEAD")
        return TestResult(ran=True, passed=True)

    monkeypatch.setattr(runner, "run_tests", inspect)
    assert engine.check(str(tmp_path), staged=True, coverage_policy="test-only").passed
    (tmp_path / "node_modules" / "example" / "index.js").unlink()
    (tmp_path / "node_modules" / "example").rmdir()
    (tmp_path / "node_modules").rmdir()
    verdict = engine.check(str(tmp_path), staged=True, coverage_policy="test-only")
    assert not verdict.passed
    assert "installed, ignored node_modules" in " ".join(verdict.reasons)


def test_staged_js_junction_cannot_escape_installed_dependencies(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows directory junction regression")
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    packages = tmp_path / "node_modules"
    packages.mkdir()
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    junction = packages / "escape"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             check=False, capture_output=True, text=True)
    if created.returncode:
        pytest.skip("junction creation is not supported: " + created.stderr)
    try:
        assert not junction.is_symlink()  # junctions are not symlinks on Python 3.11
        with pytest.raises(ValueError, match="node_modules link escapes"):
            engine._prepare_staged_dependencies(str(tmp_path), str(tmp_path / "snapshot"), {"js"})
    finally:
        junction.rmdir()  # never recursively remove a junction target
        outside.rmdir()


def test_staged_snapshot_junction_cannot_escape(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows directory junction regression")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    junction = snapshot / "escape"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             check=False, capture_output=True, text=True)
    if created.returncode:
        pytest.skip("junction creation is not supported: " + created.stderr)
    try:
        assert not junction.is_symlink()
        with pytest.raises(ValueError, match="staged snapshot link escapes"):
            engine._prepare_staged_dependencies(str(tmp_path), str(snapshot), {"python"})
    finally:
        junction.rmdir()
        outside.rmdir()


def test_staged_js_package_root_junction_cannot_escape(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows directory junction regression")
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    junction = tmp_path / "node_modules"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             check=False, capture_output=True, text=True)
    if created.returncode:
        pytest.skip("junction creation is not supported: " + created.stderr)
    try:
        with pytest.raises(ValueError, match="installed, ignored node_modules"):
            engine._prepare_staged_dependencies(str(tmp_path), str(tmp_path / "snapshot"), {"js"})
    finally:
        junction.rmdir()
        outside.rmdir()


def test_staged_python_only_does_not_require_js_dependencies(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.js").write_text("const x = 1;\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "start")
    (tmp_path / "app.py").write_text("x = 2\n")
    _git(tmp_path, "add", "app.py")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True))
    assert engine.check(str(tmp_path), staged=True, coverage_policy="test-only").passed
    (tmp_path / "app.js").write_text("const x = 2;\n")
    _git(tmp_path, "add", "app.js")
    verdict = engine.check(str(tmp_path), staged=True, coverage_policy="test-only")
    assert not verdict.passed
    assert "installed, ignored node_modules" in " ".join(verdict.reasons)


def test_staged_safe_symlink_allowed_and_escaping_symlink_blocked(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.symlinks", "true")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "alias.py").symlink_to("app.py")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "app.py").write_text("value = 2\n")
    _git(tmp_path, "add", "app.py")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True))
    assert engine.check(str(tmp_path), staged=True, coverage_policy="test-only").passed
    (tmp_path / "alias.py").unlink()
    (tmp_path / "alias.py").symlink_to("../outside.py")
    _git(tmp_path, "add", "alias.py")
    verdict = engine.check(str(tmp_path), staged=True, coverage_policy="test-only")
    assert not verdict.passed
    assert any("link escapes snapshot" in reason for reason in verdict.reasons)


def test_failed_worktree_remove_cleans_only_snapshot(monkeypatch, tmp_path):
    from proofofwork.core import gitdiff

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "app.py").write_text("value = 2\n")
    _git(tmp_path, "add", "app.py")
    unrelated = tmp_path / "unrelated"
    _git(tmp_path, "worktree", "add", "--detach", str(unrelated), "HEAD")
    shutil.rmtree(unrelated)  # missing worktree must remain registered, not globally pruned
    before = {line for line in _git(tmp_path, "worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True))
    original = gitdiff._git
    snapshots = []

    def remove_fails(root, *args):
        if args[:3] == ("worktree", "remove", "--force"):
            snapshots.append(args[3])
            raise RuntimeError("remove failed")
        if args[:2] == ("worktree", "prune"):
            raise AssertionError("global prune must never run")
        return original(root, *args)

    monkeypatch.setattr(gitdiff, "_git", remove_fails)
    verdict = engine.check(str(tmp_path), staged=True)
    assert not verdict.passed
    assert any("remove failed" in reason for reason in verdict.reasons)
    assert len(snapshots) == 2  # both targeted attempts failed
    assert snapshots[0] == snapshots[1]
    assert not Path(snapshots[0]).exists()
    listed = {line for line in _git(tmp_path, "worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    assert before <= listed  # unrelated missing worktree remains registered
    assert len(listed) == len(before) + 1  # failed removal left snapshot metadata


def test_failed_worktree_remove_retry_unregisters_snapshot(monkeypatch, tmp_path):
    from proofofwork.core import gitdiff

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "app.py").write_text("value = 2\n")
    _git(tmp_path, "add", "app.py")
    unrelated = tmp_path / "unrelated"
    _git(tmp_path, "worktree", "add", "--detach", str(unrelated), "HEAD")
    shutil.rmtree(unrelated)
    before = {line for line in _git(tmp_path, "worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(ran=True, passed=True))
    original = gitdiff._git
    snapshots = []

    def fail_once(root, *args):
        if args[:3] == ("worktree", "remove", "--force"):
            snapshots.append(args[3])
            if len(snapshots) == 1:
                raise RuntimeError("first remove failed")
            assert not Path(args[3]).exists()
        if args[:2] == ("worktree", "prune"):
            raise AssertionError("global prune must never run")
        return original(root, *args)

    monkeypatch.setattr(gitdiff, "_git", fail_once)
    verdict = engine.check(str(tmp_path), staged=True, coverage_policy="test-only")
    assert verdict.passed, verdict.reasons
    assert len(snapshots) == 2 and snapshots[0] == snapshots[1]
    listed = {line for line in _git(tmp_path, "worktree", "list", "--porcelain").splitlines()
              if line.startswith("worktree ")}
    assert listed == before  # no stale snapshot; unrelated registration retained


def test_staged_mutation_fails_closed_without_touching_worktree(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "app.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "app.py").write_text("value = 2\n")
    _git(tmp_path, "add", "app.py")
    (tmp_path / "app.py").write_text("value = 3\n")
    def must_not_run(*args):
        raise AssertionError("mutation ran on worktree")
    monkeypatch.setattr("proofofwork.core.mutation.run_mutation", must_not_run)
    verdict = engine.check(str(tmp_path), staged=True, run_tests=False, run_mutation=True)
    assert not verdict.passed
    assert any(f.rule == "mutation-staged-unavailable" for f in verdict.findings)
    assert (tmp_path / "app.py").read_text() == "value = 3\n"


def test_malformed_baseline_blocks_even_without_coverage(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True))
    baseline = tmp_path / ".proofofwork" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text("not json")
    _commit_baseline(tmp_path)
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed
    assert any(f.rule == "coverage-baseline-invalid" for f in verdict.findings)
    assert not any(f.rule == "coverage-baseline-missing" for f in verdict.findings)


def test_extra_block_still_enforced_in_detector_only_mode(monkeypatch, tmp_path):
    _init_empty(tmp_path)
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    verdict = engine.check(str(tmp_path), run_tests=False,
                           extra_findings=[Finding("protected", Severity.BLOCK, "modified")])
    assert not verdict.passed
