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
            pass
    if staged:
        from ..gitdiff import _git
        try:
            _git(root, "cat-file", "-e", "HEAD:.proofofwork/baseline.json")
            return True
        except RuntimeError:
            pass
    return os.path.lexists(baseline_path(root, staged=staged))


def read_baseline(root: str, *, staged: bool = False,
                  base_ref: str | None = None) -> float | None:
    try:
        if base_ref is not None:
            from ..gitdiff import _git
            content = _git(root, "show", f"{base_ref}:.proofofwork/baseline.json")
            value = json.loads(content)["coverage"]
        elif staged:
            from ..gitdiff import _git
            try:
                content = _git(root, "show", "HEAD:.proofofwork/baseline.json")
            except RuntimeError:
                with open(baseline_path(root, staged=True), encoding="utf-8") as f:
                    content = f.read()
            value = json.loads(content)["coverage"]
        else:
            with open(baseline_path(root), encoding="utf-8") as f:
                value = json.load(f)["coverage"]
        return float(value) if valid_coverage(value) else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def valid_coverage(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 100)


def write_baseline(root: str, coverage: float, *, staged: bool = False) -> None:
    if not valid_coverage(coverage):
        raise ValueError("coverage must be a finite percentage between 0 and 100")
    path = baseline_path(root, staged=staged)
    directory = os.path.dirname(path)
    if os.path.islink(directory):
        raise ValueError("coverage baseline directory must not be a symlink")
    os.makedirs(directory, exist_ok=True)
    # Exclusive creation: never replace even a malformed or concurrently created baseline.
    with open(path, "x", encoding="utf-8") as f:
        json.dump({"coverage": coverage}, f)


def coverage_findings(tests: TestResult, baseline: float | None, *,
                      threshold: float = 2.0) -> list[Finding]:
    if tests.coverage is not None and not valid_coverage(tests.coverage):
        return [Finding(rule="coverage-invalid", severity=Severity.BLOCK,
                        message="measured coverage must be a finite percentage between 0 and 100")]
    if baseline is not None and not valid_coverage(baseline):
        return [Finding(rule="coverage-baseline-invalid", severity=Severity.BLOCK,
                        message="coverage baseline must be a finite percentage between 0 and 100")]
    if baseline is None:
        return [Finding(
            rule="coverage-baseline-missing",
            severity=Severity.BLOCK if tests.passed is True and tests.coverage is not None
            else Severity.INFO,
            message=("no coverage baseline recorded; run with --update-baseline after passing "
                     "tests to bootstrap one" if tests.coverage is not None
                     else "coverage unavailable; no coverage baseline recorded or enforced"))]

    if tests.passed is True and tests.coverage is None:
        return [Finding(rule="coverage-unavailable", severity=Severity.INFO,
                        message="coverage unavailable; baseline comparison not enforced")]

    if (tests.passed is True and tests.coverage is not None
            and (baseline - tests.coverage) > threshold):
        return [Finding(
            rule="coverage-drop", severity=Severity.BLOCK,
            message=(f"tests pass but coverage fell {baseline - tests.coverage:.1f} pts "
                     f"({baseline:.1f}% -> {tests.coverage:.1f}%), over {threshold} threshold"),
            evidence=f"baseline={baseline} current={tests.coverage}")]

    return []
