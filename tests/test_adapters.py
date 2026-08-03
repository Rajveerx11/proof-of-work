from __future__ import annotations

import json
import sys

import pytest

from proofofwork.eval import build_agent_invocation, load_task, run_task
from proofofwork.eval.adapters import AdapterValidationError


def _task(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    path = tmp_path / "task.yaml"
    path.write_text(
        """version: 1
id: adapter-test
fixture: fixture
category: bug-fix
difficulty: easy
corpus_version: 0.2.0
instruction: Set VALUE to 42 in app.py.
expected:
  argv: ["{python}", -c, "from app import VALUE; assert VALUE == 42"]
  timeout_seconds: 10
""",
        encoding="utf-8",
    )
    return load_task(path)


def test_codex_adapter_generates_reviewed_argv_without_running_codex(monkeypatch):
    monkeypatch.setattr(
        "proofofwork.eval.adapters.shutil.which",
        lambda name: "/tools/codex" if name == "codex" else None,
    )

    invocation = build_agent_invocation("codex", model="gpt-test", agent_label="Codex A")

    assert invocation.agent_label == "Codex A"
    assert invocation.model_label == "gpt-test"
    assert invocation.argv == (
        "/tools/codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--config",
        'approval_policy="never"',
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--cd",
        "{workspace}",
        "--model",
        "gpt-test",
        (
            "Read TASK.md, make the requested changes inside the current workspace, "
            "run relevant checks, and stop when the task is complete."
        ),
    )
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation.argv


def test_codex_adapter_requires_explicit_opt_in_for_unrestricted_mode(monkeypatch):
    monkeypatch.setattr(
        "proofofwork.eval.adapters.shutil.which",
        lambda name: "/tools/codex" if name == "codex" else None,
    )

    invocation = build_agent_invocation("codex", trusted_unrestricted=True)

    assert "--dangerously-bypass-approvals-and-sandbox" in invocation.argv
    assert "--sandbox" not in invocation.argv
    assert "workspace-write" not in invocation.argv
    assert invocation.agent_label == "codex [trusted-unrestricted]"


def test_claude_adapter_generates_reviewed_argv_without_running_claude(monkeypatch):
    monkeypatch.setattr(
        "proofofwork.eval.adapters.shutil.which",
        lambda name: "/tools/claude" if name == "claude" else None,
    )

    invocation = build_agent_invocation("claude", model="claude-test")

    assert invocation.argv[:6] == (
        "/tools/claude",
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        "--add-dir",
        "{workspace}",
    )
    assert invocation.argv[6:] == ("--permission-mode", "acceptEdits", "--model", "claude-test", (
        "Read TASK.md, make the requested changes inside the current workspace, "
        "run relevant checks, and stop when the task is complete."
    ))
    assert "--dangerously-skip-permissions" not in invocation.argv


def test_claude_adapter_requires_explicit_opt_in_for_unrestricted_mode():
    invocation = build_agent_invocation(
        "claude",
        executable="/tools/claude",
        trusted_unrestricted=True,
    )

    assert "--dangerously-skip-permissions" in invocation.argv
    assert "--permission-mode" not in invocation.argv
    assert invocation.agent_label == "claude [trusted-unrestricted]"


def test_generic_adapter_rejects_unrestricted_mode():
    with pytest.raises(AdapterValidationError, match="only valid with codex or claude"):
        build_agent_invocation(
            "generic",
            generic_argv_json='["agent", "{workspace}"]',
            trusted_unrestricted=True,
        )


def test_generic_adapter_preserves_trusted_argv_list():
    raw = json.dumps(["managed-agent", "--workspace", "{workspace}", "--usage", "{usage}"])

    invocation = build_agent_invocation(
        "generic",
        generic_argv_json=raw,
        model="managed-model",
        agent_label="Managed agent",
    )

    assert invocation.argv == (
        "managed-agent",
        "--workspace",
        "{workspace}",
        "--usage",
        "{usage}",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"generic_argv_json": "not-json"},
        {"generic_argv_json": "{}"},
        {"generic_argv_json": '["agent", "{workspace}"]', "executable": "agent"},
    ],
)
def test_generic_adapter_rejects_malformed_configuration(kwargs):
    with pytest.raises(AdapterValidationError):
        build_agent_invocation("generic", **kwargs)


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_missing_adapter_executable_is_a_failed_result_without_cli_installation(tmp_path, adapter):
    invocation = build_agent_invocation(
        adapter,
        executable="definitely-not-an-installed-agent",
    )

    result = run_task(_task(tmp_path), invocation.argv, agent_timeout_seconds=10)

    assert not result.passed
    assert result.agent.exit_code is None
    assert "failed to start" in result.agent.stderr


def test_generic_adapter_timeout_is_contained(tmp_path):
    invocation = build_agent_invocation(
        "generic",
        generic_argv_json=json.dumps(
            [sys.executable, "-c", "import time; time.sleep(10)", "{workspace}"]
        ),
    )

    result = run_task(_task(tmp_path), invocation.argv, agent_timeout_seconds=1)

    assert not result.passed
    assert result.agent.timed_out


def test_generic_adapter_nonzero_exit_fails_even_if_workspace_was_fixed(tmp_path):
    script = (
        "from pathlib import Path; "
        "Path('app.py').write_text('VALUE = 42\\n'); "
        "raise SystemExit(7)"
    )
    invocation = build_agent_invocation(
        "generic",
        generic_argv_json=json.dumps([sys.executable, "-c", script, "{workspace}"]),
    )

    result = run_task(_task(tmp_path), invocation.argv, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert result.agent.exit_code == 7
    assert not result.passed


def test_generic_adapter_malformed_usage_stays_unknown_not_zero(tmp_path):
    script = (
        "import sys; from pathlib import Path; "
        "Path('app.py').write_text('VALUE = 42\\n'); "
        "Path(sys.argv[2]).write_text('[]')"
    )
    invocation = build_agent_invocation(
        "generic",
        generic_argv_json=json.dumps(
            [sys.executable, "-c", script, "{workspace}", "{usage}"]
        ),
    )

    result = run_task(_task(tmp_path), invocation.argv, agent_timeout_seconds=10)

    assert result.passed
    assert result.usage is None
    assert result.usage_error is not None
