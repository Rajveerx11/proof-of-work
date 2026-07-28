"""Run a trusted local task in a disposable workspace.

This is not a sandbox. Only run reviewed fixtures and agent commands. A container or
microVM is required before evaluating untrusted inputs.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .task import EvalTask

OUTPUT_LIMIT = 8_000


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True)
class EvalResult:
    task_id: str
    agent: ProcessResult
    outcome: ProcessResult
    passed: bool
    verification: str = "outcome-command"

    def as_dict(self) -> dict:
        return asdict(self)


def run_task(task: EvalTask, agent_argv: list[str] | tuple[str, ...], *, agent_timeout_seconds: int = 600) -> EvalResult:
    """Run a task with a CLI-selected agent argv containing exactly `{workspace}`.

    YAML never controls the agent executable. The task's outcome command is executed
    as an argv list with no shell, in a new temporary copy of the reviewed fixture.
    """
    argv = _agent_argv(agent_argv)
    if not isinstance(agent_timeout_seconds, int) or not 1 <= agent_timeout_seconds <= 3600:
        raise ValueError("agent_timeout_seconds must be an integer from 1 to 3600")
    _validate_fixture(task.fixture)

    with tempfile.TemporaryDirectory(prefix="proof-of-work-eval-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        shutil.copytree(task.fixture, workspace, symlinks=False)
        shutil.rmtree(workspace / ".git", ignore_errors=True)
        (workspace / "TASK.md").write_text(task.instruction + "\n", encoding="utf-8")

        resolved_agent_argv = [str(workspace) if item == "{workspace}" else item for item in argv]
        agent = _run(resolved_agent_argv, workspace, agent_timeout_seconds)
        outcome = _run(list(task.expected.argv), workspace, task.expected.timeout_seconds)
        passed = not agent.timed_out and agent.exit_code == 0 and not outcome.timed_out and outcome.exit_code == 0
        return EvalResult(task.id, agent, outcome, passed)


def _agent_argv(argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("agent argv must be a non-empty list of strings")
    placeholders = [item for item in argv if "{" in item or "}" in item]
    if placeholders != ["{workspace}"]:
        raise ValueError("agent argv must contain exactly one standalone '{workspace}' placeholder")
    return tuple(argv)


def _validate_fixture(fixture: Path) -> None:
    if fixture.is_symlink():
        raise ValueError("fixture directory must not be a symlink")
    for path in fixture.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"fixture contains a symlink: {path.relative_to(fixture)}")


def _run(argv: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
    started = time.monotonic()
    kwargs: dict = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": _safe_environment(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        return ProcessResult(None, time.monotonic() - started, False, "", f"failed to start: {exc}")
    stdout = _OutputCollector(process.stdout)
    stderr = _OutputCollector(process.stderr)
    stdout.start()
    stderr.start()
    try:
        process.wait(timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        process.wait(timeout=5)
        timed_out = True
    stdout.join(timeout=0.2)
    stderr.join(timeout=0.2)
    return ProcessResult(process.returncode, time.monotonic() - started, timed_out,
                         stdout.text(), stderr.text())


def _safe_environment() -> dict[str, str]:
    keys = ["PATH"]
    if os.name == "nt":
        keys.extend(["SYSTEMROOT", "COMSPEC"])
    return {key: os.environ[key] for key in keys if key in os.environ}


def _terminate_process_group(process: subprocess.Popen) -> None:
    if os.name == "nt":
        # /T is essential: kill descendants that may still hold the output pipes.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class _OutputCollector(threading.Thread):
    """Drain a process pipe without retaining unbounded agent-controlled output."""

    def __init__(self, pipe) -> None:
        super().__init__(daemon=True)
        self._pipe = pipe
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def run(self) -> None:
        try:
            while chunk := self._pipe.read(4096):
                with self._lock:
                    self._buffer.extend(chunk)
                    if len(self._buffer) > OUTPUT_LIMIT:
                        del self._buffer[:-OUTPUT_LIMIT]
        finally:
            self._pipe.close()

    def text(self) -> str:
        with self._lock:
            return bytes(self._buffer).decode("utf-8", errors="replace")
