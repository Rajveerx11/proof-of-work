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
class GatePolicy:
    protected_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalTask:
    id: str
    fixture: Path
    instruction: str
    expected: ExpectedOutcome
    gate: GatePolicy = GatePolicy()


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
    _only_keys(
        data,
        {"version", "id", "fixture", "instruction", "expected", "gate"},
        "task",
        required={"version", "id", "fixture", "instruction", "expected"},
    )
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
    gate = _parse_gate(data.get("gate"), fixture)
    return EvalTask(
        task_id,
        fixture,
        instruction,
        ExpectedOutcome(tuple(argv), timeout),
        gate,
    )


def _parse_gate(value: Any, fixture: Path) -> GatePolicy:
    if value is None:
        return GatePolicy()
    if not isinstance(value, dict):
        raise TaskValidationError("gate must be a mapping")
    _only_keys(value, {"protected_paths"}, "gate")
    paths = value.get("protected_paths")
    if not isinstance(paths, list) or not paths:
        raise TaskValidationError("gate.protected_paths must be a non-empty list")

    normalized: list[str] = []
    for item in paths:
        raw = _nonempty_string(item, "gate.protected_paths item")
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise TaskValidationError(
                "gate.protected_paths items must be relative paths without '..'"
            )
        resolved = (fixture / candidate).resolve()
        source = fixture / candidate
        try:
            resolved.relative_to(fixture)
        except ValueError as exc:
            raise TaskValidationError(
                f"gate.protected_paths item escapes fixture: {raw}"
            ) from exc
        if source.is_symlink() or not resolved.is_file():
            raise TaskValidationError(
                f"gate.protected_paths item must be a regular fixture file: {raw}"
            )
        path = candidate.as_posix()
        if path in normalized:
            raise TaskValidationError(f"duplicate gate.protected_paths item: {raw}")
        normalized.append(path)
    return GatePolicy(tuple(normalized))


def _only_keys(
    data: dict[str, Any],
    allowed: set[str],
    name: str,
    *,
    required: set[str] | None = None,
) -> None:
    unknown = set(data) - allowed
    missing = (allowed if required is None else required) - set(data)
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
