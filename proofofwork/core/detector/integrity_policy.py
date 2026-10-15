"""Trusted caller enforcement; never read policy/approval from a candidate repository."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from ...types import Finding, Severity

HIGH_RISK_INTEGRITY_RULES = frozenset({
    "deleted-test", "test-path-removed", "removed-test-fn", "added-skip", "removed-assert",
    "weak-assert", "weakened-assert", "integrity-analysis-incomplete",
})


def apply_integrity_policy(findings: Iterable[Finding], *, strict: bool = True) -> list[Finding]:
    """Copy findings, promoting high-risk WARNs in strict mode; never authorize overrides.

    ``strict=False`` preserves advisory severity, including existing BLOCKs. The
    trusted caller owns review, mode selection, verdict calculation and evidence.
    """
    if type(strict) is not bool:
        raise TypeError("strict must be an explicit bool")
    return [replace(f, severity=Severity.BLOCK)
            if strict and f.rule in HIGH_RISK_INTEGRITY_RULES and f.severity == Severity.WARN
            else replace(f) for f in findings]
