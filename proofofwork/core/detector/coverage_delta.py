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
                           base_ref: str | None = None, local_only: bool = False) -> dict | None:
    try:
        if base_ref is not None:
            from ..gitdiff import _git
            content = _git(root, "show", f"{base_ref}:.proofofwork/baseline.json")
        elif staged and not local_only:
            from ..gitdiff import _git
            try:
                content = _git(root, "show", "HEAD:.proofofwork/baseline.json")
            except RuntimeError:
                with open(baseline_path(root, staged=True), encoding="utf-8") as f:
                    content = f.read()
        else:
            with open(baseline_path(root, staged=staged), encoding="utf-8") as f:
                content = f.read()
        data = json.loads(content)
        return data if isinstance(data, dict) else None
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
    if identity is not None and identity not in (["python"], ["js"], ["js", "python"]):
        raise ValueError("coverage identity must be a sorted supported language family list")
    path = baseline_path(root, staged=staged)
    directory = os.path.dirname(path)
    if os.path.islink(directory):
        raise ValueError("coverage baseline directory must not be a symlink")
    os.makedirs(directory, exist_ok=True)
    # Exclusive creation: never replace even a malformed or concurrently created baseline.
    with open(path, "x", encoding="utf-8") as f:
        data = {"coverage": coverage}
        if identity is not None:
            data.update(metric=METRIC, languages=identity)
        json.dump(data, f)


def coverage_findings(tests: TestResult, baseline: float | None, *,
                      threshold: float = 2.0, require_coverage: bool = False) -> list[Finding]:
    # Legacy callers keep their informational missing-coverage behavior.
    if require_coverage:
        if not valid_coverage(threshold):
            return [Finding("coverage-policy-invalid", Severity.BLOCK,
                            "coverage threshold must be a finite percentage between 0 and 100")]
        if not coverage_identity(tests):
            return [Finding("coverage-incomparable", Severity.BLOCK,
                            "required coverage has no supported metric/language identity")]
        values = [tests.coverage]
        if "+" in tests.framework:
            values.append(tests.js_coverage)
        if any(v is not None and not valid_coverage(v) for v in values):
            return [Finding("coverage-invalid", Severity.BLOCK,
                            "required coverage must be a finite percentage between 0 and 100")]
        if any(v is None for v in values):
            return [Finding("coverage-unavailable", Severity.BLOCK,
                            "fresh coverage required for every executed language suite")]
        if baseline is None:
            return [Finding("coverage-baseline-missing", Severity.BLOCK,
                            "no trusted coverage baseline; bootstrap on a reviewed tree")]
        if not valid_coverage(baseline):
            return [Finding("coverage-baseline-invalid", Severity.BLOCK,
                            "trusted coverage baseline must be a finite percentage between 0 and 100")]
        return [Finding("coverage-drop", Severity.BLOCK,
                        f"tests pass but coverage fell {baseline - value:.1f} pts "
                        f"({baseline:.1f}% -> {value:.1f}%), over {threshold} threshold",
                        evidence=f"baseline={baseline} current={value}")
                for value in values if tests.passed is True and baseline - value > threshold]
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
