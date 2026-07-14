"""Re-run the real suite through the sandbox and read the TRUE result.

Never the agent's word: pass/fail comes from the process exit code, coverage from the
tool's own JSON. Always routes through `sandbox.run(...)` so isolation stays swappable.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from ..types import TestResult
from .sandbox import RunOutput, Sandbox

_TAIL = 2000  # chars of output kept for debugging


def _tail(out: RunOutput) -> str:
    return (out.stdout + out.stderr)[-_TAIL:]


def run_tests(sandbox: Sandbox, root: str, languages: set[str]) -> TestResult:
    if "python" in languages:
        r = _run_python(sandbox, root)
        if r is not None:
            return r
    if "js" in languages or "ts" in languages:
        r = _run_js(sandbox, root)
        if r is not None:
            return r
    return TestResult(ran=False)


# --- Python: pytest, coverage via coverage.py if present ---
def _has_module(sandbox: Sandbox, root: str, mod: str) -> bool:
    out = sandbox.run([sys.executable, "-c", f"import {mod}"], cwd=root, timeout=30)
    return out.code == 0


def _run_python(sandbox: Sandbox, root: str) -> TestResult | None:
    if not _has_module(sandbox, root, "pytest"):
        return None  # no recognizable python test tooling

    if _has_module(sandbox, root, "coverage"):
        # system temp, not `root`: `coverage json -o` takes an absolute path, so the
        # file never appears as an untracked entry inside the repo under test.
        cov_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cov_file.close()
        try:
            run = sandbox.run(
                [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q"],
                cwd=root)
            sandbox.run(
                [sys.executable, "-m", "coverage", "json", "-o", cov_file.name],
                cwd=root, timeout=120)
            coverage = _read_coverage_json(cov_file.name)
            return TestResult(ran=True, passed=(run.code == 0), coverage=coverage,
                              framework="pytest", raw=_tail(run))
        finally:
            try:
                os.unlink(cov_file.name)
            except OSError:
                pass

    # coverage.py absent — exit-code only
    run = sandbox.run([sys.executable, "-m", "pytest", "-q"], cwd=root)
    return TestResult(ran=True, passed=(run.code == 0), coverage=None,
                      framework="pytest", raw=_tail(run))


def _read_coverage_json(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["totals"]["percent_covered"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- JS/TS: vitest / jest ---
def _run_js(sandbox: Sandbox, root: str) -> TestResult | None:
    pkg_path = os.path.join(root, "package.json")
    try:
        with open(pkg_path, encoding="utf-8") as f:
            pkg = json.load(f)
    except (OSError, ValueError):
        return None

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    scripts = " ".join(pkg.get("scripts", {}).values())
    blob = " ".join(deps) + " " + scripts

    if "vitest" in blob:
        run = sandbox.run(["npx", "vitest", "run", "--coverage"], cwd=root)
        cov = _read_js_summary(
            os.path.join(root, "coverage", "coverage-summary.json"))
        return TestResult(ran=True, passed=(run.code == 0), coverage=cov,
                          framework="vitest", raw=_tail(run))
    if "jest" in blob:
        run = sandbox.run(["npx", "jest", "--coverage", "--json"], cwd=root)
        cov = _read_js_summary(
            os.path.join(root, "coverage", "coverage-summary.json"))
        return TestResult(ran=True, passed=(run.code == 0), coverage=cov,
                          framework="jest", raw=_tail(run))
    return None  # package.json present but no recognized test runner


def _read_js_summary(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["total"]["lines"]["pct"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
