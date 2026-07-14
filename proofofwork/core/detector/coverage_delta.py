"""Coverage-delta check: a suite can pass while quietly covering far less than before.

Baseline lives in .proofofwork/baseline.json. A material drop while tests still pass is
a BLOCK — that's the shape of gutted-but-green tests.
"""
from __future__ import annotations

import json
import os

from ...types import Finding, Severity, TestResult

_BASELINE = os.path.join(".proofofwork", "baseline.json")


def read_baseline(root: str) -> float | None:
    try:
        with open(os.path.join(root, _BASELINE), encoding="utf-8") as f:
            return float(json.load(f)["coverage"])
    except (OSError, ValueError, KeyError, TypeError):
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
            rule="coverage-baseline-missing", severity=Severity.INFO,
            message="no coverage baseline recorded; run with --update-baseline to set one")]

    if (tests.passed is True and tests.coverage is not None
            and (baseline - tests.coverage) > threshold):
        return [Finding(
            rule="coverage-drop", severity=Severity.BLOCK,
            message=(f"tests pass but coverage fell {baseline - tests.coverage:.1f} pts "
                     f"({baseline:.1f}% -> {tests.coverage:.1f}%), over {threshold} threshold"),
            evidence=f"baseline={baseline} current={tests.coverage}")]

    return []
