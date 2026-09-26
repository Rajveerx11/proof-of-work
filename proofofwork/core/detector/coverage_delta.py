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


def read_baseline(root: str, *, staged: bool = False) -> float | None:
    try:
        if staged:
            from ..gitdiff import _git
            content = _git(root, "show", "HEAD:.proofofwork/baseline.json")
            value = float(json.loads(content)["coverage"])
        else:
            with open(os.path.join(root, _BASELINE), encoding="utf-8") as f:
                value = float(json.load(f)["coverage"])
        return value if math.isfinite(value) and 0 <= value <= 100 else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def write_baseline(root: str, coverage: float) -> None:
    d = os.path.join(root, ".proofofwork")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "baseline.json"), "w", encoding="utf-8") as f:
        json.dump({"coverage": coverage}, f)


def coverage_findings(tests: TestResult, baseline: float | None, *,
                      threshold: float = 2.0) -> list[Finding]:
    if baseline is None:
        return [Finding(
            rule="coverage-baseline-missing",
            severity=Severity.BLOCK if tests.passed is True and tests.coverage is not None
            else Severity.INFO,
            message=("no coverage baseline recorded; run with --update-baseline after passing "
                     "tests to bootstrap one" if tests.coverage is not None
                     else "coverage unavailable; no coverage baseline recorded or enforced"))]

    if (tests.passed is True and tests.coverage is not None
            and (baseline - tests.coverage) > threshold):
        return [Finding(
            rule="coverage-drop", severity=Severity.BLOCK,
            message=(f"tests pass but coverage fell {baseline - tests.coverage:.1f} pts "
                     f"({baseline:.1f}% -> {tests.coverage:.1f}%), over {threshold} threshold"),
            evidence=f"baseline={baseline} current={tests.coverage}")]

    return []
