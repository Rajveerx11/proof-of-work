"""Trusted argv-list adapters for supported coding-agent CLIs."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass

ADAPTERS = ("codex", "claude", "generic")
_AGENT_PROMPT = (
    "Read TASK.md, make the requested changes inside the current workspace, "
    "run relevant checks, and stop when the task is complete."
)


class AdapterValidationError(ValueError):
    """Raised when a trusted adapter configuration is invalid."""


@dataclass(frozen=True)
class AgentInvocation:
    adapter: str
    agent_label: str
    model_label: str | None
    argv: tuple[str, ...]


def build_agent_invocation(
    adapter: str,
    *,
    generic_argv_json: str | None = None,
    executable: str | None = None,
    model: str | None = None,
    agent_label: str | None = None,
) -> AgentInvocation:
    """Build one reviewed argv list without invoking or requiring an installed CLI."""
    if adapter not in ADAPTERS:
        raise AdapterValidationError(f"adapter must be one of: {', '.join(ADAPTERS)}")
    parsed_model = _optional_label(model, "model")
    parsed_agent_label = _optional_label(agent_label, "agent_label") or adapter

    if adapter == "generic":
        if executable is not None:
            raise AdapterValidationError("generic adapter does not accept --agent-executable")
        if generic_argv_json is None:
            raise AdapterValidationError("generic adapter requires --agent-argv-json")
        try:
            argv = json.loads(generic_argv_json)
        except json.JSONDecodeError as exc:
            raise AdapterValidationError(f"agent argv is not valid JSON: {exc}") from exc
        if not isinstance(argv, list):
            raise AdapterValidationError("agent argv JSON must be a list")
        return AgentInvocation(adapter, parsed_agent_label, parsed_model, tuple(argv))

    if generic_argv_json is not None:
        raise AdapterValidationError(
            "--agent-argv-json is only valid with the generic adapter"
        )
    command = _executable(executable, adapter)
    if adapter == "codex":
        argv = [
            command,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            "{workspace}",
        ]
        if parsed_model is not None:
            argv.extend(("--model", parsed_model))
        argv.append(_AGENT_PROMPT)
    else:
        argv = [
            command,
            "--print",
            "--permission-mode",
            "acceptEdits",
            "--no-session-persistence",
            "--add-dir",
            "{workspace}",
        ]
        if parsed_model is not None:
            argv.extend(("--model", parsed_model))
        argv.append(_AGENT_PROMPT)
    return AgentInvocation(adapter, parsed_agent_label, parsed_model, tuple(argv))


def _executable(value: str | None, default: str) -> str:
    if value is not None:
        parsed = _label(value, "executable")
        if "{" in parsed or "}" in parsed:
            raise AdapterValidationError("executable must not contain placeholders")
        return parsed
    # Resolve Windows .cmd launchers before the runner strips PATHEXT from its
    # minimal child environment. Missing CLIs remain a normal failed-agent result.
    return shutil.which(default) or default


def _optional_label(value: str | None, name: str) -> str | None:
    return None if value is None else _label(value, name)


def _label(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterValidationError(f"{name} must be a non-empty string")
    if len(value) > 200 or any(character in value for character in "\r\n\0"):
        raise AdapterValidationError(f"{name} must be one line of at most 200 characters")
    return value
