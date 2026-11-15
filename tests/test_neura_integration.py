"""CLI integration contract for an operator controlling an offline candidate gate."""
import json
import os
import subprocess

import pytest
import test_neura_gitdiff as git_fixtures

from proofofwork import __version__, engine
from proofofwork.core import gitdiff, suites
from proofofwork.interfaces.cli import main
from proofofwork.types import TestResult

repo = git_fixtures.repo


def invoke(root, capsys, *options):
    code = main(["check", "--root", str(root), "--json", *options])
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err + captured.out
    return code, json.loads(captured.out)


@pytest.mark.parametrize("cheat", [False, True])
def test_untracked_detectors_and_json_contract(repo, capsys, cheat):
    (repo / "test_added.py").write_text(
        "import sys\nsys." "exit(0)\n" if cheat else
        "from tracked import value\ndef test_ok():\n    assert value == 1\n")
    base = gitdiff._git(str(repo), "rev-parse", "HEAD").strip()
    before = git_fixtures.metadata(repo)
    code, data = invoke(repo, capsys, "--base", base, "--no-tests", "--include-untracked")
    assert code == (1 if cheat else 0)
    assert data["passed"] is (not cheat)
    assert data["base_commit"] == base
    assert type(data["schema_version"]) is int and data["schema_version"] == 1
    assert data["tool_version"] == __version__
    assert data["tool_error"] == ""
    assert git_fixtures.metadata(repo) == before
    assert any(f["rule"].startswith("fake-pass") for f in data["findings"]) is cheat


@pytest.mark.parametrize("phase", ["status", "patch", "untracked"])
def test_late_git_failure_never_becomes_a_pass(repo, capsys, monkeypatch, phase):
    original = gitdiff.subprocess.run

    def broken(argv, **kwargs):
        if ((phase == "status" and "--name-status" in argv)
                or (phase == "patch" and gitdiff._FULL_CONTEXT in argv)
                or (phase == "untracked" and "--others" in argv)):
            return subprocess.CompletedProcess(argv, 128, b"", b"fatal: injected Git failure")
        return original(argv, **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "run", broken)
    code, data = invoke(repo, capsys, "--no-tests", "--include-untracked")
    assert code == 3 and not data["passed"]
    assert "Git failure" in data["tool_error"]
    assert data["entry_hash"] == ""


@pytest.mark.parametrize("failure", ["missing-repository", "missing-directory", "bad-base"])
def test_git_input_errors_have_distinct_exit(repo, tmp_path, capsys, failure):
    root = repo if failure == "bad-base" else tmp_path
    if failure == "missing-directory":
        root = tmp_path / "absent"
    code, data = invoke(root, capsys, "--no-tests", "--base",
                        "nonexistent" if failure == "bad-base" else "HEAD")
    assert code == 3 and not data["passed"] and data["tool_error"]
    assert data["base_commit"] == ""


@pytest.mark.parametrize("options", [["--unknown"], ["--coverage-policy", "invalid"], ["--base"]])
def test_unusable_check_arguments_exit_three(capsys, options):
    with pytest.raises(SystemExit) as error:
        main(["check", *options])
    assert error.value.code == 3
    assert "error:" in capsys.readouterr().err


def test_incompatible_flags_exit_three(repo, capsys):
    code, data = invoke(repo, capsys, "--no-tests", "--staged", "--include-untracked")
    assert code == 3 and "cannot combine" in data["tool_error"]


@pytest.mark.parametrize("json_output", [True, False])
def test_unexpected_engine_crash_has_no_traceback(monkeypatch, capsys, json_output):
    def crash(**kwargs):
        raise RuntimeError("injected internal failure")

    monkeypatch.setattr(engine, "check", crash)
    assert main(["check", *(["--json"] if json_output else [])]) == 3
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    if json_output:
        assert "injected internal failure" in json.loads(captured.out)["tool_error"]
    else:
        assert "injected internal failure" in captured.err


def external_config(tmp_path, *, exit_code=0):
    path = tmp_path / "trusted-suites.json"
    path.write_text(json.dumps({"version": 1, "suites": [{
        "id": "operator", "cwd": ".", "argv": ["python", "-c", f"raise SystemExit({exit_code})"],
        "prerequisites": [], "timeout": 5, "evidence": "exit-code",
    }]}), encoding="utf-8")
    return path


@pytest.mark.parametrize("exit_code", [0, 7])
def test_external_suites_override_candidate_and_suite_base(repo, tmp_path, capsys, exit_code):
    path = external_config(tmp_path, exit_code=exit_code)
    (repo / ".proofofwork").mkdir()
    (repo / suites.CONFIG).write_text("malformed candidate-controlled JSON")
    code, data = invoke(repo, capsys, "--suites-file", str(path), "--suite-base", "missing-ref",
                        "--coverage-policy", "test-only")
    assert code == (0 if exit_code == 0 else 1), data
    assert data["passed"] is (exit_code == 0)
    assert data["tests"]["suite_base"] == ""
    assert data["tests"]["required_suites"] == data["tests"]["executed_suites"] == ["operator"]
    assert data["tests"]["suites"][0]["exit_code"] == exit_code


@pytest.mark.parametrize("bad", ["missing", "malformed", "shell", "string-argv", "duplicate-key"])
def test_external_suite_validation_fails_closed(repo, tmp_path, capsys, bad):
    path = external_config(tmp_path)
    if bad == "missing":
        path.unlink()
    elif bad == "malformed":
        path.write_text("not JSON")
    elif bad == "duplicate-key":
        path.write_text('{"version":1,"version":1,"suites":[]}')
    else:
        config = json.loads(path.read_text())
        config["suites"][0]["argv"] = ["sh", "-c", "true"] if bad == "shell" else "node -e true"
        path.write_text(json.dumps(config))
    code, data = invoke(repo, capsys, "--suites-file", str(path), "--coverage-policy", "test-only")
    assert code == 3 and not data["passed"] and data["tool_error"]
    assert data["tests"]["executed_suites"] == []


@pytest.mark.parametrize("policy", ["required", "test-only"])
def test_external_suites_cannot_bypass_committed_baseline(repo, tmp_path, capsys, policy):
    path = external_config(tmp_path)
    baseline = repo / ".proofofwork/baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(json.dumps({"coverage": 95, "metric": "project-lines-v1", "languages": ["python"]}))
    gitdiff._git(str(repo), "add", "-A")
    gitdiff._git(str(repo), "commit", "-qm", "trusted floor")
    base = gitdiff._git(str(repo), "rev-parse", "HEAD").strip()
    baseline.unlink()
    code, data = invoke(repo, capsys, "--base", base, "--suites-file", str(path), "--coverage-policy", policy)
    assert code == 1 and not data["passed"]
    assert data["tests"]["executed_suites"] == []
    assert any("coverage baseline" in f["message"] for f in data["findings"])


@pytest.mark.parametrize("candidate_floor", [1, 100, None])
@pytest.mark.parametrize("coverage", [90, 100])
def test_explicit_sha_uses_committed_floor(repo, monkeypatch, capsys, candidate_floor, coverage):
    baseline = repo / ".proofofwork/baseline.json"
    baseline.parent.mkdir()
    floor = {"coverage": 95, "metric": "project-lines-v1", "languages": ["python"]}
    baseline.write_text(json.dumps(floor))
    gitdiff._git(str(repo), "add", "-A")
    gitdiff._git(str(repo), "commit", "-qm", "trusted floor")
    base = gitdiff._git(str(repo), "rev-parse", "HEAD").strip()
    if candidate_floor is None:
        baseline.unlink()
    else:
        baseline.write_text(json.dumps({**floor, "coverage": candidate_floor}))
    # Commit the candidate so the explicit base is demonstrably different from HEAD.
    (repo / "tracked.py").write_text("value = 2\n")
    gitdiff._git(str(repo), "add", "-A")
    gitdiff._git(str(repo), "commit", "-qm", "candidate")
    monkeypatch.setattr("proofofwork.core.runner.run_tests", lambda *a, **k:
                        TestResult(ran=True, passed=True, framework="pytest", coverage=coverage))
    code, data = invoke(repo, capsys, "--base", base)
    assert code == (0 if coverage == 100 else 1), data["reasons"]
    assert data["coverage_baseline"] == 95
    assert data["coverage_baseline_source"] == data["base_commit"] == base


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
def test_worktree_setup_errors_are_specific_and_distinct(repo, capsys, monkeypatch, staged, readonly):
    (repo / "tracked.py").write_text("value = 2\n")
    gitdiff._git(str(repo), "add", "tracked.py")
    original = gitdiff.subprocess.run

    def execute(argv, **kwargs):
        if readonly and "worktree" in argv and "add" in argv:
            return subprocess.CompletedProcess(argv, 128, b"", b"Read-only file system")
        return original(argv, **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "run", execute)
    monkeypatch.setattr("proofofwork.core.runner.run_tests", lambda *a, **k:
                        TestResult(ran=True, passed=True, framework="pytest", coverage=100))
    options = ["--staged", "--coverage-policy", "test-only"] if staged else ["--measure-base-coverage"]
    code, data = invoke(repo, capsys, *options)
    assert code == (3 if readonly else 0), data["reasons"]
    if readonly:
        assert not data["passed"] and "read-only .git" in data["tool_error"]
    else:
        assert data["passed"] and data["tool_error"] == ""


@pytest.mark.parametrize("staged", [False, True])
def test_real_nonwritable_git_base_measurement(repo, capsys, staged):
    paths = [repo / ".git", *(repo / ".git").rglob("*")]
    modes = {path: path.stat().st_mode for path in paths}
    try:
        for path in paths:
            path.chmod(modes[path] & ~0o222)
        if os.access(repo / ".git", os.W_OK):
            # Windows readonly attributes/root do not enforce POSIX directory permissions.
            before = git_fixtures.metadata(repo)
            code, data = invoke(repo, capsys, "--no-tests", "--include-untracked")
            assert code == 0 and data["passed"]
            assert git_fixtures.metadata(repo) == before
        else:
            options = (["--staged", "--coverage-policy", "test-only"] if staged
                       else ["--measure-base-coverage"])
            code, data = invoke(repo, capsys, *options)
            assert code == 3 and not data["passed"]
            assert "read-only .git" in data["tool_error"]
    finally:
        for path, mode in modes.items():
            path.chmod(mode)


@pytest.mark.parametrize("broken", [False, True])
def test_log_failure_cannot_report_pass_or_verdict_failure(repo, capsys, monkeypatch, broken):
    if broken:
        def fail(*args, **kwargs):
            raise OSError("log not writable")

        monkeypatch.setattr("proofofwork.log.record", fail)
    code, data = invoke(repo, capsys, "--no-tests")
    assert code == (3 if broken else 0)
    assert data["passed"] is (not broken)
    if broken:
        assert "log not writable" in data["tool_error"]
        assert data["entry_hash"] == ""


@pytest.mark.parametrize("scenario,expected", [
    ("malformed", 3), ("missing", 3), ("no-ref", 3),
    ("valid", 0), ("policy", 1), ("test-failed", 1),
])
def test_committed_suite_load_errors_are_distinct_from_verdicts(repo, tmp_path, capsys, scenario, expected):
    path = external_config(tmp_path, exit_code=7 if scenario == "test-failed" else 0)
    config = repo / suites.CONFIG
    config.parent.mkdir()
    if scenario != "missing":
        config.write_bytes(b"malformed" if scenario == "malformed" else path.read_bytes())
    gitdiff._git(str(repo), "add", "-A")
    gitdiff._git(str(repo), "commit", "--allow-empty", "-qm", "suite policy")
    options = [] if scenario == "no-ref" else ["--suite-base", "HEAD"]
    code, data = invoke(repo, capsys, *options, "--coverage-policy",
                        "required" if scenario == "policy" else "test-only")
    assert code == expected
    assert data["passed"] is (expected == 0)
    assert bool(data["tool_error"]) is (expected == 3)
    assert data["tests"]["ran"] is (scenario in {"valid", "test-failed"})


@pytest.mark.parametrize("dependency_state,passed,expected", [
    ("missing", True, 3), ("not-ignored", True, 3),
    ("installed", True, 0), ("installed", False, 1),
])
@pytest.mark.parametrize("staged", [False, True])
def test_staged_dependencies_distinguish_setup_from_test_failure(
        repo, capsys, monkeypatch, dependency_state, passed, expected, staged):
    (repo / "package.json").write_text('{"private": true}')
    (repo / "package-lock.json").write_text('{}')
    (repo / "app.js").write_text("const value = 1;\n")
    gitdiff._git(str(repo), "add", "-A")
    gitdiff._git(str(repo), "commit", "-qm", "base dependency inputs")
    (repo / "app.js").write_text("const value = 2;\n")
    if dependency_state != "missing":
        packages = repo / "node_modules"
        packages.mkdir()
        (packages / "dependency.js").write_text("module.exports = {};\n")
    if dependency_state == "installed":
        (repo / ".gitignore").write_text("node_modules/\n.proofofwork/log*\n")
    gitdiff._git(str(repo), "add", "app.js", ".gitignore")
    executions = []

    def run(*args, **kwargs):
        executions.append(args)
        return TestResult(ran=True, passed=passed, framework="jest", coverage=100)

    monkeypatch.setattr("proofofwork.core.runner.run_tests", run)
    options = ["--staged", "--coverage-policy", "test-only"] if staged else ["--measure-base-coverage"]
    code, data = invoke(repo, capsys, *options)
    assert code == expected
    assert data["passed"] is (expected == 0)
    assert bool(data["tool_error"]) is (expected == 3)
    assert bool(executions) is (dependency_state == "installed")
    if expected == 3:
        assert "node_modules" in data["tool_error"]
