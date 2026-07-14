"""Optional mutation testing — a bounded sanity poke, never a gate.

ponytail: mutation is v1-optional and bounded. One timed run through the sandbox; if the
tool is absent, times out, or its output can't be parsed, we degrade to ran=False rather
than block or hang. Ceiling: real mutation coverage (targeted mutants, per-file budgets,
incremental) is a v2 concern — wire it here behind the same MutationResult shape.
"""
from __future__ import annotations

import re
import sys

from ..types import MutationResult
from .sandbox import RunOutput
from .sandbox.local import LocalSandbox

_MUT_TIMEOUT = 300  # seconds — hard ceiling; a slow suite must not stall the gate


def run_mutation(root: str, languages: set[str]) -> MutationResult:
    sandbox = LocalSandbox()
    if "python" in languages:
        r = _run_mutmut(sandbox, root)
        if r is not None:
            return r
    if "js" in languages or "ts" in languages:
        r = _run_stryker(sandbox, root)
        if r is not None:
            return r
    return MutationResult(ran=False)


def _run_mutmut(sandbox: LocalSandbox, root: str) -> MutationResult | None:
    probe = sandbox.run([sys.executable, "-c", "import mutmut"], cwd=root, timeout=30)
    if probe.code != 0:
        return None  # mutmut not installed — skip silently

    out = sandbox.run([sys.executable, "-m", "mutmut", "run"], cwd=root,
                      timeout=_MUT_TIMEOUT)
    if out.timed_out:
        return MutationResult(ran=False, tool="mutmut")

    counts = _parse_mutmut(out)
    if counts is None:
        return MutationResult(ran=False, tool="mutmut")
    killed, survived = counts
    return MutationResult(ran=True, killed=killed, survived=survived, tool="mutmut")


def _parse_mutmut(out: RunOutput) -> tuple[int, int] | None:
    """mutmut's summary uses emoji tallies: 🎉 = killed, 🙁 = survived.

    ponytail: emoji-scrape is brittle across mutmut versions; good enough for the
    optional path. Upgrade to `mutmut junitxml` parsing if this becomes load-bearing.
    """
    text = out.stdout + out.stderr
    killed = re.search(r"🎉\s*(\d+)", text)
    survived = re.search(r"🙁\s*(\d+)", text)
    if killed is None and survived is None:
        return None
    return (int(killed.group(1)) if killed else 0,
            int(survived.group(1)) if survived else 0)


def _run_stryker(sandbox: LocalSandbox, root: str) -> MutationResult | None:
    import os
    if not os.path.exists(os.path.join(root, "node_modules", ".bin",
                                       "stryker" + (".cmd" if os.name == "nt" else ""))):
        return None  # only if trivially present locally — no global install hunt
    out = sandbox.run(["npx", "stryker", "run"], cwd=root, timeout=_MUT_TIMEOUT)
    if out.timed_out:
        return MutationResult(ran=False, tool="stryker")
    text = out.stdout + out.stderr
    killed = re.search(r"[Kk]illed:\s*(\d+)", text)
    survived = re.search(r"[Ss]urvived:\s*(\d+)", text)
    if killed is None and survived is None:
        return MutationResult(ran=False, tool="stryker")
    return MutationResult(
        ran=True, tool="stryker",
        killed=int(killed.group(1)) if killed else 0,
        survived=int(survived.group(1)) if survived else 0)
