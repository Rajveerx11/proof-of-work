"""Coverage-delta check: a suite can pass while quietly covering far less than before.

Baseline lives in .proofofwork/baseline.json. A material drop while tests still pass is
a BLOCK — that's the shape of gutted-but-green tests.
"""
from __future__ import annotations

import json
import math
import os

from ...types import Finding, Severity, TestResult

_BASELINE = os.path.join(".proofofwork", "baseline.json")


def baseline_path(root: str, *, staged: bool = False) -> str:
    if staged:
        from ..gitdiff import _git
        return os.path.abspath(os.path.join(root, _git(root, "rev-parse", "--git-path",
                                                       "proofofwork/baseline.json").strip()))
    return os.path.join(root, _BASELINE)


def baseline_exists(root: str, *, staged: bool = False,
                    base_ref: str | None = None) -> bool:
    if base_ref is not None:
        from ..gitdiff import _git
        try:
            _git(root, "cat-file", "-e", f"{base_ref}:.proofofwork/baseline.json")
            return True
        except RuntimeError:
            return False
    if staged:
        from ..gitdiff import _git
        try:
            _git(root, "cat-file", "-e", "HEAD:.proofofwork/baseline.json")
            return True
        except RuntimeError:
            pass
    return os.path.lexists(baseline_path(root, staged=staged))


METRIC = "project-lines-v1"


def coverage_identity(tests: TestResult) -> list[str]:
    frameworks = tests.framework.split("+")
    if any(f not in {"pytest", "vitest", "jest"} for f in frameworks):
        return []
    return sorted({"python" if f == "pytest" else "js" for f in frameworks})


def read_baseline_metadata(root: str, *, staged: bool = False,
                           base_ref: str | None = None) -> dict | None:
    try:
        if base_ref is not None:
            from ..gitdiff import _git
            content = _git(root, "show", f"{base_ref}:.proofofwork/baseline.json")
            return json.loads(content)
        elif staged:
            from ..gitdiff import _git
            try:
                content = _git(root, "show", "HEAD:.proofofwork/baseline.json")
            except RuntimeError:
                with open(baseline_path(root, staged=True), encoding="utf-8") as f:
                    content = f.read()
            return json.loads(content)
        else:
            with open(baseline_path(root), encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def read_baseline(root: str, *, staged: bool = False,
                  base_ref: str | None = None) -> float | None:
    data = read_baseline_metadata(root, staged=staged, base_ref=base_ref)
    value = data.get("coverage") if isinstance(data, dict) else None
    return float(value) if valid_coverage(value) else None


def valid_coverage(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 100 and math.isfinite(value))


def write_baseline(root: str, coverage: float, *, staged: bool = False,
                   identity: list[str] | None = None) -> None:
    if not valid_coverage(coverage):
        raise ValueError("coverage must be a finite percentage between 0 and 100")
    path = baseline_path(root, staged=staged)
    directory = os.path.dirname(path)
    if os.path.islink(directory):
        raise ValueError("coverage baseline directory must not be a symlink")
    os.makedirs(directory, exist_ok=True)
    # Exclusive creation: never replace even a malformed or concurrently created baseline.
    with open(path, "x", encoding="utf-8") as f:
        json.dump({"coverage": coverage, "metric": METRIC,
                   "languages": identity or ["python"]}, f)


def coverage_findings(tests: TestResult, baseline: float | None, *,
                      threshold: float = 2.0, bootstrap: bool = False) -> list[Finding]:
    findings = []
    if not valid_coverage(threshold) or threshold > 2.0:
        return [Finding("coverage-policy-invalid", Severity.BLOCK,
                        "coverage threshold must be finite and between 0 and 2 points")]
    values = [tests.coverage]
    if "+" in tests.framework:
        values.append(tests.js_coverage)
    if any(v is not None and not valid_coverage(v) for v in values):
        findings.append(Finding("coverage-invalid", Severity.BLOCK,
                                "measured coverage must be a finite percentage between 0 and 100"))
    if tests.passed is True and any(v is None for v in values):
        findings.append(Finding("coverage-unavailable", Severity.BLOCK,
                                "fresh coverage required for every executed language suite"))
    if baseline is not None and not valid_coverage(baseline):
        findings.append(Finding("coverage-baseline-invalid", Severity.BLOCK,
                                "coverage baseline must be a finite percentage between 0 and 100"))
    elif baseline is None and not bootstrap:
        findings.append(Finding("coverage-baseline-missing", Severity.BLOCK,
                                "no trusted coverage baseline; explicitly bootstrap on a reviewed "
                                "tree or opt in to --measure-base-coverage"))
    elif baseline is not None and tests.passed is True:
        for value in values:
            if valid_coverage(value) and baseline - value > threshold:
                findings.append(Finding("coverage-drop", Severity.BLOCK,
                                        f"tests pass but coverage fell {baseline - value:.1f} pts "
                                        f"({baseline:.1f}% -> {value:.1f}%), over {threshold} threshold",
                                        evidence=f"baseline={baseline} current={value}"))
    return findings
