"""Trusted-local subprocess executor, NOT a security sandbox.

Commands inherit host environment and can access host files and network. Keep this
backend for reviewed code only; snapshot fidelity does not provide containment.
"""
from __future__ import annotations

import os
import subprocess

from . import RunOutput


class LocalSandbox:
    name = "local"

    def run(self, cmd: list[str], cwd: str, env: dict | None = None,
            timeout: int = 600) -> RunOutput:
        full_env = {**os.environ, **(env or {})}
        try:
            p = subprocess.run(
                cmd, cwd=cwd, env=full_env, timeout=timeout,
                capture_output=True, text=True, check=False,
            )
            return RunOutput(code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        except subprocess.TimeoutExpired as e:
            return RunOutput(
                code=124,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                timed_out=True,
            )
