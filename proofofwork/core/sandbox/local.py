"""Local subprocess sandbox — the v1 isolation driver.

ponytail: subprocess is v1 isolation. Run from the committed tree, it isolates the
suite against uncommitted tampering (the agent can't reach outside argv/cwd/env here).
Docker/microVM is the v2 driver behind this same `Sandbox` Protocol — swap via get_sandbox.
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
                capture_output=True, text=True,
            )
            return RunOutput(code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        except subprocess.TimeoutExpired as e:
            return RunOutput(
                code=124,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                timed_out=True,
            )
