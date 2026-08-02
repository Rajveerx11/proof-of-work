"""Run a trusted local task in a disposable workspace.

This is not a sandbox. Only run reviewed fixtures and agent commands. A container or
microVM is required before evaluating untrusted inputs.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..types import Verdict
from .gate import gate_failure, score_gate, snapshot_workspace
from .task import EvalTask

OUTPUT_LIMIT = 8_000
USAGE_FILE_LIMIT = 64 * 1024
_MAX_SQLITE_INTEGER = 2**63 - 1
_MAX_COST_USD_MICROS = 2**53 - 1
_USD_MICROS = Decimal(1_000_000)
_MAX_COST_USD = Decimal(_MAX_COST_USD_MICROS) / _USD_MICROS
_START_ERROR_MARKER = "__PROOFOFWORK_START_ERROR__"
_WINDOWS_PROCESS_WRAPPER = """
import json
import os
import subprocess
import sys
import time

argv = json.loads(os.environ.pop("PROOFOFWORK_WRAPPED_ARGV"))
ready = os.environ.pop("PROOFOFWORK_JOB_READY")
while not os.path.exists(ready):
    time.sleep(0.001)
try:
    completed = subprocess.run(argv, stdin=subprocess.DEVNULL, shell=False, check=False)
except OSError as exc:
    print("__PROOFOFWORK_START_ERROR__" + f"failed to start: {exc}", file=sys.stderr)
    raise SystemExit(127)
raise SystemExit(completed.returncode)
"""
_ISOLATED_PYTHON_SCRIPT = """
import os
import runpy
import sys

script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, os.getcwd())
runpy.run_path(script, run_name="__main__")
"""


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True)
class UsageMetrics:
    input_tokens: int
    output_tokens: int
    cost_usd_micros: int

    def __post_init__(self) -> None:
        _token_count("input_tokens", self.input_tokens)
        _token_count("output_tokens", self.output_tokens)
        if (
            not isinstance(self.cost_usd_micros, int)
            or isinstance(self.cost_usd_micros, bool)
            or not 0 <= self.cost_usd_micros <= _MAX_COST_USD_MICROS
        ):
            raise ValueError("cost_usd_micros must be a non-negative exact integer")
        if self.input_tokens + self.output_tokens > _MAX_SQLITE_INTEGER:
            raise ValueError("total token count is too large")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return self.cost_usd_micros / 1_000_000

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class EvalResult:
    task_id: str
    agent: ProcessResult
    outcome: ProcessResult
    passed: bool
    verification: str = "outcome-command+proof-of-work-gate"
    gate: Verdict | None = None
    usage: UsageMetrics | None = None
    usage_error: str | None = None
    agent_label: str | None = None
    model_label: str | None = None
    category: str | None = None
    difficulty: str | None = None
    corpus_version: str | None = None
    wall_time_seconds: float | None = None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": asdict(self.agent),
            "outcome": asdict(self.outcome),
            "gate": self.gate.as_dict() if self.gate is not None else None,
            "usage": self.usage.as_dict() if self.usage is not None else None,
            "usage_error": self.usage_error,
            "agent_label": self.agent_label,
            "model_label": self.model_label,
            "category": self.category,
            "difficulty": self.difficulty,
            "corpus_version": self.corpus_version,
            "wall_time_seconds": self.wall_time_seconds,
            "passed": self.passed,
            "verification": self.verification,
        }


def run_task(
    task: EvalTask,
    agent_argv: list[str] | tuple[str, ...],
    *,
    agent_timeout_seconds: int = 600,
    agent_label: str | None = None,
    model_label: str | None = None,
) -> EvalResult:
    """Run a task with a CLI-selected agent argv containing exactly `{workspace}`.

    YAML never controls the agent executable. The task's outcome command is executed
    as an argv list with no shell, in a new temporary copy of the reviewed fixture.
    """
    argv = _agent_argv(agent_argv)
    if not isinstance(agent_timeout_seconds, int) or not 1 <= agent_timeout_seconds <= 3600:
        raise ValueError("agent_timeout_seconds must be an integer from 1 to 3600")
    _validate_fixture(task.fixture)
    evaluation_started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="proof-of-work-eval-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        shutil.copytree(task.fixture, workspace, symlinks=False)
        shutil.rmtree(workspace / ".git", ignore_errors=True)
        (workspace / "TASK.md").write_text(task.instruction + "\n", encoding="utf-8")

        usage_path = Path(temp_dir) / "usage.json"
        resolved_agent_argv = [
            str(workspace)
            if item == "{workspace}"
            else str(usage_path)
            if item == "{usage}"
            else item
            for item in argv
        ]
        agent = _run(resolved_agent_argv, workspace, agent_timeout_seconds)
        captured_workspace = Path(temp_dir) / "captured-workspace"
        outcome_workspace = Path(temp_dir) / "outcome-workspace"
        try:
            # Capture once. Gate and verifier receive independent copies of this same
            # post-agent state, so a surviving child cannot create a check/use race by
            # continuing to edit the original workspace.
            snapshot_workspace(workspace, captured_workspace)
            snapshot_workspace(captured_workspace, outcome_workspace)
        except (OSError, ValueError) as exc:
            gate = gate_failure(f"could not capture agent workspace: {exc}")
            outcome = ProcessResult(
                None,
                0.0,
                False,
                "",
                f"not run: could not capture agent workspace: {exc}",
            )
        else:
            gate = score_gate(
                task.fixture,
                captured_workspace,
                Path(temp_dir) / "gate-workspace",
                instruction=task.instruction,
                db_path=Path(temp_dir) / "gate-log.db",
                protected_paths=task.gate.protected_paths,
            )
            outcome = _run(
                _outcome_argv(task.expected.argv),
                outcome_workspace,
                task.expected.timeout_seconds,
            )
        passed = (
            not agent.timed_out
            and agent.exit_code == 0
            and not outcome.timed_out
            and outcome.exit_code == 0
            and gate.passed
        )
        usage = None
        usage_error = None
        if "{usage}" in argv:
            try:
                usage = _load_usage_metrics(usage_path)
            except (OSError, TypeError, ValueError) as exc:
                usage_error = str(exc)
        result = EvalResult(
            task.id,
            agent,
            outcome,
            passed,
            gate=gate,
            usage=usage,
            usage_error=usage_error,
            agent_label=agent_label,
            model_label=model_label,
            category=task.category,
            difficulty=task.difficulty,
            corpus_version=task.corpus_version,
        )
    return replace(result, wall_time_seconds=time.monotonic() - evaluation_started)


def _outcome_argv(argv: tuple[str, ...]) -> list[str]:
    """Resolve the only task-defined executable placeholder to this interpreter."""
    if (
        len(argv) >= 2
        and argv[0] == "{python}"
        and not argv[1].startswith("-")
        and Path(argv[1]).suffix.lower() == ".py"
    ):
        # Start direct verifier scripts in isolated mode before adding the workspace
        # import path. Agent-created sitecustomize.py/usercustomize.py cannot run
        # ahead of a protected verifier and turn it into a fake pass.
        return [sys.executable, "-I", "-c", _ISOLATED_PYTHON_SCRIPT, *argv[1:]]
    return [sys.executable if item == "{python}" else item for item in argv]


def _agent_argv(argv: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("agent argv must be a non-empty list of strings")
    placeholders = [item for item in argv if "{" in item or "}" in item]
    if placeholders.count("{workspace}") != 1:
        raise ValueError("agent argv must contain exactly one standalone '{workspace}' placeholder")
    if placeholders.count("{usage}") > 1 or any(
        item not in {"{workspace}", "{usage}"} for item in placeholders
    ):
        raise ValueError("agent argv may contain one optional standalone '{usage}' placeholder")
    return tuple(argv)


def _load_usage_metrics(path: Path) -> UsageMetrics:
    """Read bounded metrics emitted by the trusted operator-owned agent wrapper."""
    if not path.exists():
        raise ValueError("agent argv used '{usage}' but did not write the usage JSON file")
    if path.is_symlink() or not path.is_file():
        raise ValueError("usage JSON must be a regular file")
    if path.stat().st_size > USAGE_FILE_LIMIT:
        raise ValueError(f"usage JSON must not exceed {USAGE_FILE_LIMIT} bytes")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=Decimal,
            parse_constant=_invalid_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read usage JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"input_tokens", "output_tokens", "cost_usd"}:
        raise ValueError(
            "usage JSON must contain exactly input_tokens, output_tokens, and cost_usd"
        )
    input_tokens = _token_count("input_tokens", data["input_tokens"])
    output_tokens = _token_count("output_tokens", data["output_tokens"])
    if input_tokens + output_tokens > _MAX_SQLITE_INTEGER:
        raise ValueError("total token count is too large")
    cost_usd_micros = _cost_usd_micros(data["cost_usd"])
    return UsageMetrics(input_tokens, output_tokens, cost_usd_micros)


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON number: {value}")


def _token_count(name: str, value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _cost_usd_micros(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise TypeError("cost_usd must be a non-negative JSON number with at most 6 decimals")
    try:
        cost = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("cost_usd must be a non-negative number with at most 6 decimals") from exc
    if (
        not cost.is_finite()
        or cost.as_tuple().exponent < -6
        or cost < 0
        or cost > _MAX_COST_USD
    ):
        raise ValueError("cost_usd must be a non-negative number with at most 6 decimals")
    return int(cost * _USD_MICROS)


def _validate_fixture(fixture: Path) -> None:
    if fixture.is_symlink():
        raise ValueError("fixture directory must not be a symlink")
    for path in fixture.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"fixture contains a symlink: {path.relative_to(fixture)}")


def _run(argv: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
    started = time.monotonic()
    environment = _safe_environment()
    launch_argv = argv
    ready_path: Path | None = None
    if os.name == "nt":
        descriptor, raw_ready_path = tempfile.mkstemp(prefix="proof-of-work-job-", suffix=".ready")
        os.close(descriptor)
        os.unlink(raw_ready_path)
        ready_path = Path(raw_ready_path)
        environment["PROOFOFWORK_WRAPPED_ARGV"] = json.dumps(argv)
        environment["PROOFOFWORK_JOB_READY"] = str(ready_path)
        # Wrapper waits for the ready file. Parent assigns it to a kill-on-close Job
        # Object first, so the evaluated command cannot race out of containment.
        launch_argv = [_wrapper_python_executable(), "-I", "-c", _WINDOWS_PROCESS_WRAPPER]
    kwargs: dict = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": environment,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(launch_argv, **kwargs)
    except OSError as exc:
        _remove_ready_file(ready_path)
        return ProcessResult(None, time.monotonic() - started, False, "", f"failed to start: {exc}")
    job = None
    if os.name == "nt":
        try:
            job = _WindowsJob(process)
            ready_path.touch(exist_ok=False)
        except OSError as exc:
            if job is not None:
                job.close()
            _terminate_process_group(process)
            process.wait(timeout=5)
            _remove_ready_file(ready_path)
            return ProcessResult(
                None,
                time.monotonic() - started,
                False,
                "",
                f"failed to contain process: {exc}",
            )
    stdout = _OutputCollector(process.stdout)
    stderr = _OutputCollector(process.stderr)
    stdout.start()
    stderr.start()
    try:
        process.wait(timeout=timeout_seconds)
        timed_out = False
        _terminate_finished_process_group(process)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        process.wait(timeout=5)
        timed_out = True
    if job is not None:
        job.close()
    stdout.join(timeout=0.2)
    stderr.join(timeout=0.2)
    _remove_ready_file(ready_path)
    stderr_text = stderr.text()
    exit_code = process.returncode
    if exit_code == 127 and _START_ERROR_MARKER in stderr_text:
        exit_code = None
        stderr_text = stderr_text.replace(_START_ERROR_MARKER, "", 1)
    return ProcessResult(exit_code, time.monotonic() - started, timed_out,
                         stdout.text(), stderr_text)


def _remove_ready_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _wrapper_python_executable() -> str:
    """Use the real interpreter, not a venv launcher that may spawn before Job assignment."""
    base_executable = getattr(sys, "_base_executable", None)
    if isinstance(base_executable, str) and base_executable:
        return base_executable
    return sys.executable


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


def _terminate_finished_process_group(process: subprocess.Popen) -> None:
    """Stop surviving descendants after a normally exited group leader where supported."""
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP has no Windows equivalent of POSIX killpg after
        # its leader exits. Snapshot isolation still prevents descendants from
        # changing the scored/verifier copies.
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _WindowsJob:
    """Kill all process descendants when the evaluated command exits."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        try:
            information = ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle,
                self._EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            process_handle = wintypes.HANDLE(int(process._handle))
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


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
