from __future__ import annotations

import sys
from pathlib import Path

import pytest

from proofofwork.eval import TaskValidationError, load_task, run_task


def _task_file(tmp_path, *, fixture="fixture", extra=""):
    fixture_dir = tmp_path / fixture
    fixture_dir.mkdir(exist_ok=True)
    (fixture_dir / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    task = tmp_path / "task.yaml"
    task.write_text(
        """version: 1
id: python-fix-001
fixture: fixture
instruction: Set VALUE to 42 in app.py.
expected:
  argv: [python, -c, \"from app import VALUE; assert VALUE == 42\"]
  timeout_seconds: 10
""" + extra,
        encoding="utf-8",
    )
    return task


def test_run_task_uses_fresh_workspace_and_records_outcome(tmp_path):
    task_path = _task_file(tmp_path)
    task = load_task(task_path)
    agent = [sys.executable, "-c", "from pathlib import Path; Path('app.py').write_text('VALUE = 42\\n')", "{workspace}"]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.passed
    assert result.verification == "outcome-command"
    assert result.agent.exit_code == 0
    assert result.outcome.exit_code == 0
    assert (tmp_path / "fixture" / "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"


def test_shipped_python_fixture_runs_end_to_end():
    root = Path(__file__).resolve().parents[1]
    task = load_task(root / "tasks" / "python-fix-001.yaml")
    agent = [
        sys.executable,
        "-c",
        "from pathlib import Path; p = Path('calculator.py'); p.write_text(p.read_text().replace('left - right', 'left + right'))",
        "{workspace}",
    ]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.passed
    assert result.outcome.exit_code == 0


def test_run_task_bounds_agent_output(tmp_path):
    task = load_task(_task_file(tmp_path))
    agent = [sys.executable, "-c", "print('x' * 12000); open('app.py', 'w').write('VALUE = 42\\n')", "{workspace}"]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.passed
    assert len(result.agent.stdout.encode("utf-8")) <= 8_000


def test_task_rejects_unknown_fields(tmp_path):
    task = _task_file(tmp_path, extra="unsafe_command: rm -rf /\n")

    with pytest.raises(TaskValidationError, match="unknown field"):
        load_task(task)


def test_task_rejects_path_traversal(tmp_path):
    task = _task_file(tmp_path)
    task.write_text(task.read_text(encoding="utf-8").replace("fixture: fixture", "fixture: ../fixture"), encoding="utf-8")

    with pytest.raises(TaskValidationError, match="relative path"):
        load_task(task)


def test_run_task_reports_a_missing_executable_without_raising(tmp_path):
    task = load_task(_task_file(tmp_path))

    result = run_task(task, ["definitely-not-an-agent-executable", "{workspace}"])

    assert not result.passed
    assert result.agent.exit_code is None
    assert "failed to start" in result.agent.stderr


def test_run_task_reports_a_missing_outcome_executable_without_raising(tmp_path):
    task_path = _task_file(tmp_path)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'argv: [python, -c, "from app import VALUE; assert VALUE == 42"]',
            "argv: [definitely-not-an-outcome-executable]",
        ),
        encoding="utf-8",
    )
    agent = [sys.executable, "-c", "open('app.py', 'w').write('VALUE = 42\\n')", "{workspace}"]

    result = run_task(load_task(task_path), agent)

    assert not result.passed
    assert result.outcome.exit_code is None
    assert "failed to start" in result.outcome.stderr


def test_run_task_rejects_agent_template_injection(tmp_path):
    task = load_task(_task_file(tmp_path))

    with pytest.raises(ValueError, match="exactly one standalone"):
        run_task(task, [sys.executable, "{workspace}/bad"])


def test_run_task_rejects_fixture_symlink(tmp_path):
    task_path = _task_file(tmp_path)
    linked = tmp_path / "fixture" / "linked"
    try:
        linked.symlink_to(tmp_path / "fixture" / "app.py")
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    with pytest.raises(ValueError, match="contains a symlink"):
        run_task(load_task(task_path), [sys.executable, "-c", "pass", "{workspace}"])
