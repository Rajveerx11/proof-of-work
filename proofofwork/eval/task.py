"""Strict, declarative task definitions for trusted local benchmarks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.tokens import AliasToken, AnchorToken

MAX_TASK_BYTES = 64 * 1024


class TaskValidationError(ValueError):
    """Raised when a task definition is not a supported, safe shape."""


@dataclass(frozen=True)
class ExpectedOutcome:
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class EvalTask:
    id: str
    fixture: Path
    instruction: str
    expected: ExpectedOutcome


def load_task(path: str | Path) -> EvalTask:
    """Load one task without permitting YAML object construction or aliases."""
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TaskValidationError(f"cannot read task: {exc}") from exc
    if len(raw) > MAX_TASK_BYTES:
        raise TaskValidationError(f"task exceeds {MAX_TASK_BYTES} byte limit")
    try:
        tokens = list(yaml.scan(raw))
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in tokens):
            raise TaskValidationError("YAML aliases and anchors are not supported")
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TaskValidationError(f"invalid YAML: {exc}") from exc
    return _parse_task(data, path.parent)


def _parse_task(data: Any, task_dir: Path) -> EvalTask:
    if not isinstance(data, dict):
        raise TaskValidationError("task must be a mapping")
    _only_keys(data, {"version", "id", "fixture", "instruction", "expected"}, "task")
    if data.get("version") != 1:
        raise TaskValidationError("task version must be 1")
    task_id = _nonempty_string(data.get("id"), "id")
    instruction = _nonempty_string(data.get("instruction"), "instruction")
    fixture = _fixture_path(data.get("fixture"), task_dir)
    expected = data.get("expected")
    if not isinstance(expected, dict):
        raise TaskValidationError("expected must be a mapping")
    _only_keys(expected, {"argv", "timeout_seconds"}, "expected")
    argv = expected.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise TaskValidationError("expected.argv must be a non-empty list of strings")
    timeout = expected.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        raise TaskValidationError("expected.timeout_seconds must be an integer from 1 to 3600")
    return EvalTask(task_id, fixture, instruction, ExpectedOutcome(tuple(argv), timeout))


def _only_keys(data: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(data) - allowed
    missing = allowed - set(data)
    if unknown:
        raise TaskValidationError(f"{name} has unknown field(s): {', '.join(sorted(unknown))}")
    if missing:
        raise TaskValidationError(f"{name} is missing field(s): {', '.join(sorted(missing))}")


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(f"{name} must be a non-empty string")
    return value


def _fixture_path(value: Any, task_dir: Path) -> Path:
    raw = _nonempty_string(value, "fixture")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TaskValidationError("fixture must be a relative path without '..'")
    resolved = (task_dir / candidate).resolve()
    if not resolved.is_dir():
        raise TaskValidationError("fixture must name an existing directory")
    return resolved
