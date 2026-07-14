"""Assertion tampering: tautological asserts added, real asserts net-removed."""
from __future__ import annotations

import re

from ...types import Diff, Finding, Severity

_WEAK = re.compile(
    r"\bassert\s+True\b|\bassert\s+1\b|\bassertTrue\(\s*True\s*\)"
    r"|\bexpect\(\s*true\s*\)\.toBe\(\s*true\s*\)"
)
# lines that carry a real assertion (for net-removal counting)
_ASSERT_LINE = re.compile(r"\bassert\s|\bself\.assert|\bexpect\(")


def check(diff: Diff, root: str) -> list[Finding]:
    out: list[Finding] = []
    for f in diff.files:
        for ln in f.added:
            if _WEAK.search(ln):
                out.append(Finding("weak-assert", Severity.WARN,
                                   "a tautological/weak assert was added", file=f.path,
                                   evidence=ln.strip()))
                break

        if not f.is_test:
            continue
        removed = sum(1 for ln in f.removed if _ASSERT_LINE.search(ln))
        added = sum(1 for ln in f.added if _ASSERT_LINE.search(ln))
        if removed > added:
            out.append(Finding("removed-assert", Severity.WARN,
                               f"net {removed - added} assertion line(s) removed from a test",
                               file=f.path))
    return out
