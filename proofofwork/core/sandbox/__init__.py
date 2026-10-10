"""Explicit execution backends; local subprocesses are trusted, not isolation.

Docker is an experimental, opt-in Linux backend for reviewed, metadata-free snapshots.
Backend errors never fall back to local execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RunOutput:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Sandbox(Protocol):
    name: str

    def run(self, cmd: list[str], cwd: str, env: dict | None = None,
            timeout: int = 600) -> RunOutput: ...


class SandboxError(RuntimeError):
    """Backend unavailable, unsafe input, or disposal not confirmed."""


def get_sandbox(kind: str = "local", *, image: str | None = None) -> Sandbox:
    if kind == "local":
        if image is not None:
            raise ValueError("image is only supported by the explicitly selected Docker backend")
        from .local import LocalSandbox
        return LocalSandbox()
    if kind == "docker":
        from .docker import DockerSandbox
        return DockerSandbox(image)
    raise ValueError(f"unknown sandbox {kind!r}; choose 'local' or 'docker'")
