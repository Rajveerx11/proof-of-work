"""Supervisor integration and regression checks for the three production tracks."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from proofofwork import engine
from proofofwork.eval import harness
from proofofwork.interfaces import cli
from proofofwork.types import Verdict


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "integration")
    (tmp_path / "app.py").write_text("value = 1\n")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-qm", "reviewed base")
    return tmp_path


def forbidden(*args, **kwargs):
    pytest.fail("execution must be rejected before this boundary")


def test_cli_routes_explicit_policies(monkeypatch, capsys):
    seen = {}

    def check(**kwargs):
        seen.update(kwargs)
        return Verdict(passed=True, coverage_policy=kwargs["coverage_policy"],
                       integrity_policy=kwargs["integrity_policy"])

    monkeypatch.setattr(engine, "check", check)
    assert cli.main(["check", "--coverage-policy", "test-only",
                     "--integrity-policy", "advisory", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert seen["coverage_policy"] == data["coverage_policy"] == "test-only"
    assert seen["integrity_policy"] == data["integrity_policy"] == "advisory"
    assert seen["sandbox_kind"] == "local" and seen["sandbox_image"] is None


@pytest.mark.parametrize("flag", ["--coverage-policy", "--integrity-policy", "--sandbox"])
def test_cli_rejects_unknown_policy_without_execution(monkeypatch, flag):
    monkeypatch.setattr(engine, "check", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(["check", flag, "unknown"])
    assert error.value.code == 2


def test_docker_gate_fails_before_git_or_host_execution(monkeypatch, capsys):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", forbidden)
    monkeypatch.setattr("proofofwork.core.runner.run_tests", forbidden)
    monkeypatch.setattr("proofofwork.core.mutation.run_mutation", forbidden)
    assert cli.main(["check", "--sandbox", "docker", "--mutation", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False and data["execution_mode"] == "unavailable"
    assert data["findings"][0]["rule"] == "isolation-unavailable"
    assert data["tests"]["ran"] is False


def test_local_image_does_not_silently_ignore_isolation_request(monkeypatch):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", forbidden)
    verdict = engine.check(sandbox_image="image@sha256:" + "1" * 64)
    assert not verdict.passed and verdict.findings[0].rule == "isolation-unavailable"


def test_untrusted_eval_cli_rejects_before_task_loading(monkeypatch, capsys):
    monkeypatch.setattr("proofofwork.eval.load_task", forbidden)
    monkeypatch.setattr("proofofwork.eval.build_agent_invocation", forbidden)
    monkeypatch.setattr("proofofwork.eval.run_task", forbidden)
    assert cli.main(["eval", "run", "unread-task.yaml", "--untrusted", "--no-record"]) == 2
    assert "not all isolated" in capsys.readouterr().out


@pytest.mark.parametrize("value", [True, "false", None, 1])
def test_untrusted_eval_api_rejects_before_candidate_execution(monkeypatch, value):
    monkeypatch.setattr(harness, "_agent_argv", forbidden)
    monkeypatch.setattr(harness, "_validate_fixture", forbidden)
    with pytest.raises(ValueError):
        harness.run_task(None, [], untrusted=value)


def test_explicit_base_bootstrap_cannot_certify_candidate_floor(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr("proofofwork.core.runner.run_tests", forbidden)
    monkeypatch.setattr("proofofwork.core.mutation.run_mutation", forbidden)
    verdict = engine.check(str(repo), base_ref=base, update_baseline=True, run_mutation=True)
    assert not verdict.passed and verdict.coverage_status == "not-verified"
    assert any(f.rule == "baseline-bootstrap-error" for f in verdict.findings)
    assert not (repo / ".proofofwork/baseline.json").exists()


def test_detector_only_cannot_launch_mutation(repo, monkeypatch):
    monkeypatch.setattr("proofofwork.core.mutation.run_mutation", forbidden)
    verdict = engine.check(str(repo), run_tests=False, run_mutation=True)
    assert not verdict.passed and not verdict.mutation.ran
    assert verdict.execution_mode == "detector-only"
    assert any(f.rule == "mutation-tests-required" for f in verdict.findings)


def test_coverage_bypass_rejection_also_suppresses_mutation(repo, monkeypatch):
    path = repo / ".proofofwork/baseline.json"
    path.parent.mkdir()
    path.write_text('{"coverage":90,"metric":"project-lines-v1","languages":["python"]}')
    monkeypatch.setattr("proofofwork.core.runner.run_tests", forbidden)
    monkeypatch.setattr("proofofwork.core.mutation.run_mutation", forbidden)
    verdict = engine.check(str(repo), coverage_policy="test-only", run_mutation=True)
    assert not verdict.passed and not verdict.mutation.ran and not verdict.tests.ran
    assert any(f.rule == "coverage-policy-bypass" for f in verdict.findings)
    assert any(f.rule == "mutation-execution-suppressed" for f in verdict.findings)


def action_script():
    path = Path(__file__).parents[1] / "proofofwork/interfaces/action.yml"
    action = yaml.safe_load(path.read_text())
    return next(s for s in action["runs"]["steps"] if s.get("name") == "Run gate")


@pytest.mark.skipif(os.name == "nt", reason="composite Action uses Bash on its runner")
@pytest.mark.parametrize("overrides,code", [
    ({}, 0),
    ({"COVERAGE_POLICY": "test-only", "INTEGRITY_POLICY": "advisory", "MUTATION": "true"}, 0),
    ({"COVERAGE_POLICY": "required; touch injection-marker"}, 1),
    ({"INTEGRITY_POLICY": "$(touch injection-marker)"}, 1),
    ({"MUTATION": "true; touch injection-marker"}, 1),
])
def test_action_validates_policies_as_data_and_preserves_argv(repo, overrides, code):
    step = action_script()
    assert "${{" not in step["run"]
    tools = repo / "tools"
    tools.mkdir()
    executable = tools / "proof-of-work"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > gate-argv\n")
    executable.chmod(0o755)
    base = git(repo, "rev-parse", "HEAD")
    env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
           "GITHUB_EVENT_NAME": "pull_request", "PR_BASE_SHA": base, "PUSH_BASE_SHA": "",
           "COVERAGE_POLICY": "required", "INTEGRITY_POLICY": "strict", "MUTATION": "false",
           **overrides}
    completed = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=repo, env=env,
                               capture_output=True, text=True, check=False)
    assert completed.returncode == code, completed.stderr
    assert not (repo / "injection-marker").exists()
    argv_file = repo / "gate-argv"
    if code == 0:
        expected = ["check", "--base", base, "--coverage-policy", env["COVERAGE_POLICY"],
                    "--integrity-policy", env["INTEGRITY_POLICY"]]
        if env["MUTATION"] == "true":
            expected.append("--mutation")
        assert argv_file.read_text().splitlines() == expected
    else:
        assert not argv_file.exists()
