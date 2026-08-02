from __future__ import annotations

import sys
from pathlib import Path

import pytest

from proofofwork.eval import EvalResult, TaskValidationError, UsageMetrics, load_task, run_task
from proofofwork.eval.harness import ProcessResult, _wrapper_python_executable


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


def test_eval_result_preserves_day_one_positional_constructor():
    process = ProcessResult(0, 0.1, False, "", "")

    result = EvalResult("task-1", process, process, True, "outcome-command")

    assert result.passed
    assert result.verification == "outcome-command"
    assert result.gate is None
    assert result.as_dict()["gate"] is None
    assert result.as_dict()["usage"] is None


def test_usage_metrics_are_loaded_from_trusted_wrapper_output(tmp_path):
    task = load_task(_task_file(tmp_path))
    script = (
        "import json, sys; "
        "from pathlib import Path; "
        "Path('app.py').write_text('VALUE = 42\\n'); "
        "Path(sys.argv[2]).write_text(json.dumps(dict("
        "input_tokens=1200, output_tokens=300, cost_usd=0.012345)))"
    )

    result = run_task(
        task,
        [sys.executable, "-c", script, "{workspace}", "{usage}"],
        agent_timeout_seconds=10,
    )

    assert result.passed
    assert result.usage == UsageMetrics(1200, 300, 12_345)
    assert result.as_dict()["usage"] == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500,
        "cost_usd": 0.012345,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"input_tokens": True, "output_tokens": 1, "cost_usd": 0},
        {"input_tokens": 1, "output_tokens": -1, "cost_usd": 0},
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": "0.0000001"},
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": "NaN"},
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": "sNaN"},
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": "1e999999"},
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": "1e-100000000"},
    ],
)
def test_usage_metrics_reject_malformed_wrapper_output(tmp_path, payload):
    task = load_task(_task_file(tmp_path))
    import base64
    import json

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = (
        "import base64, sys; "
        "from pathlib import Path; "
        "Path('app.py').write_text('VALUE = 42\\n'); "
        "Path(sys.argv[2]).write_bytes(base64.b64decode(sys.argv[3]))"
    )

    result = run_task(
        task,
        [sys.executable, "-c", script, "{workspace}", "{usage}", encoded],
        agent_timeout_seconds=10,
    )

    assert result.passed
    assert result.usage is None
    assert result.usage_error is not None


@pytest.mark.parametrize(
    "raw_cost",
    ["1e-100000000", "0.123456000000000001", "9007199254.740992"],
)
def test_usage_metrics_validate_raw_json_numbers_without_float_rounding(tmp_path, raw_cost):
    import base64

    task = load_task(_task_file(tmp_path))
    raw = (
        '{"input_tokens":1,"output_tokens":1,"cost_usd":' + raw_cost + "}"
    ).encode()
    encoded = base64.b64encode(raw).decode()
    script = (
        "import base64, sys; "
        "from pathlib import Path; "
        "Path('app.py').write_text('VALUE = 42\\n'); "
        "Path(sys.argv[2]).write_bytes(base64.b64decode(sys.argv[3]))"
    )

    result = run_task(
        task,
        [sys.executable, "-c", script, "{workspace}", "{usage}", encoded],
        agent_timeout_seconds=10,
    )

    assert result.passed
    assert result.usage is None
    assert result.usage_error is not None


def test_usage_placeholder_requires_wrapper_output(tmp_path):
    task = load_task(_task_file(tmp_path))
    script = "from pathlib import Path; Path('app.py').write_text('VALUE = 42\\n')"

    result = run_task(
        task,
        [sys.executable, "-c", script, "{workspace}", "{usage}"],
        agent_timeout_seconds=10,
    )

    assert result.passed
    assert result.usage is None
    assert "did not write" in result.usage_error


def test_windows_wrapper_selects_base_interpreter(monkeypatch):
    monkeypatch.setattr(sys, "_base_executable", r"C:\Python\python.exe")

    assert _wrapper_python_executable() == r"C:\Python\python.exe"


def test_run_task_uses_fresh_workspace_and_records_outcome(tmp_path):
    task_path = _task_file(tmp_path)
    task = load_task(task_path)
    agent = [sys.executable, "-c", "from pathlib import Path; Path('app.py').write_text('VALUE = 42\\n')", "{workspace}"]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.passed
    assert result.verification == "outcome-command+proof-of-work-gate"
    assert result.agent.exit_code == 0
    assert result.outcome.exit_code == 0
    assert result.gate.passed
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


def test_shipped_python_fixture_blocks_verifier_tampering():
    root = Path(__file__).resolve().parents[1]
    task = load_task(root / "tasks" / "python-fix-001.yaml")
    agent = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('verify.py').write_text('')",
        "{workspace}",
    ]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert not result.gate.passed
    assert not result.passed
    assert any(
        finding.rule == "eval:protected-path-changed"
        for finding in result.gate.findings
    )


def test_python_verifier_ignores_agent_created_sitecustomize():
    root = Path(__file__).resolve().parents[1]
    task = load_task(root / "tasks" / "python-fix-001.yaml")
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('sitecustomize.py').write_text("
            "\"from pathlib import Path; Path('verify.py').write_text('')\\n\""
            ")"
        ),
        "{workspace}",
    ]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.gate.passed
    assert result.outcome.exit_code != 0
    assert not result.passed


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


def test_task_rejects_protected_path_traversal(tmp_path):
    task = _task_file(tmp_path)
    task.write_text(
        task.read_text(encoding="utf-8")
        + "gate:\n  protected_paths: [../outside.py]\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskValidationError, match="relative path"):
        load_task(task)


def test_task_rejects_missing_protected_file(tmp_path):
    task = _task_file(tmp_path)
    task.write_text(
        task.read_text(encoding="utf-8")
        + "gate:\n  protected_paths: [missing.py]\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskValidationError, match="regular fixture file"):
        load_task(task)


def test_generated_task_file_can_be_protected_without_false_positive(tmp_path):
    task_path = _task_file(tmp_path)
    fixture = tmp_path / "fixture"
    (fixture / "TASK.md").write_text("stale fixture instruction\n", encoding="utf-8")
    task_path.write_text(
        task_path.read_text(encoding="utf-8")
        + "gate:\n  protected_paths: [TASK.md]\n",
        encoding="utf-8",
    )
    agent = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('app.py').write_text('VALUE = 42\\n')",
        "{workspace}",
    ]

    result = run_task(load_task(task_path), agent, agent_timeout_seconds=10)

    assert result.gate.passed
    assert result.outcome.exit_code == 0
    assert result.passed


def test_generated_task_file_tampering_is_blocked(tmp_path):
    task_path = _task_file(tmp_path)
    fixture = tmp_path / "fixture"
    (fixture / "TASK.md").write_text("stale fixture instruction\n", encoding="utf-8")
    task_path.write_text(
        task_path.read_text(encoding="utf-8")
        + "gate:\n  protected_paths: [TASK.md]\n",
        encoding="utf-8",
    )
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('app.py').write_text('VALUE = 42\\n'); "
            "Path('TASK.md').write_text('agent rewrite\\n')"
        ),
        "{workspace}",
    ]

    result = run_task(load_task(task_path), agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert not result.gate.passed
    assert not result.passed
    assert any(
        finding.rule == "eval:protected-path-changed"
        for finding in result.gate.findings
    )


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


def test_gate_blocks_deleted_test_even_when_expected_outcome_passes(tmp_path):
    task_path = _task_file(tmp_path)
    fixture = tmp_path / "fixture"
    (fixture / "test_guard.py").write_text(
        "def test_guard():\n    assert True\n",
        encoding="utf-8",
    )
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('app.py').write_text('VALUE = 42\\n'); "
            "Path('test_guard.py').unlink()"
        ),
        "{workspace}",
    ]

    result = run_task(load_task(task_path), agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert not result.gate.passed
    assert not result.passed
    assert any(finding.rule == "deleted-test" for finding in result.gate.findings)


def test_gate_scores_new_untracked_test_files(tmp_path):
    task = load_task(_task_file(tmp_path))
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('app.py').write_text('VALUE = 42\\n'); "
            "Path('test_fake.py').write_text('import sys\\nsys.exit(0)\\n')"
        ),
        "{workspace}",
    ]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert not result.gate.passed
    assert any(finding.rule == "fake-pass:sys-exit" for finding in result.gate.findings)


def test_gate_scores_agent_output_before_outcome_command_mutates_workspace(tmp_path):
    task_path = _task_file(tmp_path)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'argv: [python, -c, "from app import VALUE; assert VALUE == 42"]',
            'argv: [python, -c, "open(\'test_report.py\', \'w\').write(\'import sys\\\\nsys.exit(0)\\\\n\'); from app import VALUE; assert VALUE == 42"]',
        ),
        encoding="utf-8",
    )
    agent = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('app.py').write_text('VALUE = 42\\n')",
        "{workspace}",
    ]

    result = run_task(load_task(task_path), agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert result.gate.passed
    assert result.passed


def test_gate_ignores_gitattributes_that_suppress_test_diffs(tmp_path):
    task_path = _task_file(tmp_path)
    fixture = tmp_path / "fixture"
    (fixture / "test_guard.py").write_text(
        "def test_guard():\n    assert True\n",
        encoding="utf-8",
    )
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('app.py').write_text('VALUE = 42\\n'); "
            "Path('test_guard.py').write_text('import sys\\nsys.exit(0)\\n'); "
            "Path('.gitattributes').write_text('test_guard.py -diff\\n')"
        ),
        "{workspace}",
    ]

    result = run_task(load_task(task_path), agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code == 0
    assert not result.gate.passed
    assert any(finding.rule == "fake-pass:sys-exit" for finding in result.gate.findings)


def test_gate_rejects_agent_created_symlink(tmp_path):
    task = load_task(_task_file(tmp_path))
    target = tmp_path / "symlink-target.py"
    target.write_text("VALUE = 42\n", encoding="utf-8")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
        probe.unlink()
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    agent = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('app.py').unlink(); "
            f"Path('app.py').symlink_to({str(target)!r})"
        ),
        "{workspace}",
    ]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.outcome.exit_code is None
    assert "not run" in result.outcome.stderr
    assert not result.gate.passed
    assert not result.passed
    assert any(finding.rule == "eval:gate-error" for finding in result.gate.findings)


def test_surviving_agent_child_cannot_mutate_outcome_workspace():
    root = Path(__file__).resolve().parents[1]
    task = load_task(root / "tasks" / "python-fix-001.yaml")
    child_script = """
import sys
import time
from pathlib import Path

workspace = Path(sys.argv[1])
temp_root = workspace.parent
signal = temp_root / "gate-log.db"
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not signal.exists():
    time.sleep(0.01)
if signal.exists():
    (temp_root / "outcome-workspace" / "verify.py").write_text("", encoding="utf-8")
"""
    agent_script = (
        "import subprocess, sys; "
        "from pathlib import Path; "
        f"child = {child_script!r}; "
        "subprocess.Popen("
        "[sys.executable, '-c', child, str(Path.cwd())], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True)"
    )
    agent = [sys.executable, "-c", agent_script, "{workspace}"]

    result = run_task(task, agent, agent_timeout_seconds=10)

    assert result.gate.passed
    assert result.outcome.exit_code != 0
    assert not result.passed
