"""Isolation seam (N-7). v1 default = local subprocess; Docker/microVM slot in behind this.

ponytail: v1 ships only LocalSandbox (subprocess). Docker/microVM are v2 drivers that
implement the same `Sandbox` protocol — the fork stays hidden behind `get_sandbox`.
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


def get_sandbox(kind: str = "local") -> Sandbox:
    if kind == "local":
        from .local import LocalSandbox
        return LocalSandbox()
    raise ValueError(f"unknown sandbox {kind!r} (v1 supports 'local' only)")
