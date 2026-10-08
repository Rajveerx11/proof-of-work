"""Test-suite integrity: deleted/renamed test files, removed test fns, added skips."""
from __future__ import annotations

import re
from dataclasses import replace

from ...types import Diff, Finding, Severity
from ..gitdiff import _is_test

# Trusted caller policy only: never read enforcement settings from the candidate tree.
STRICT_RULES = frozenset({"test-path-removed", "removed-test-fn", "added-skip", "removed-assert"})


def apply_policy(findings: list[Finding], *, strict: bool) -> list[Finding]:
    """Blocking-only policy; do not mutate findings supplied by the caller."""
    return [replace(f, severity=Severity.BLOCK)
            if strict and f.rule in STRICT_RULES and f.severity == Severity.WARN else f
            for f in findings]


_PY_TEST_FN = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_TEST_FN = re.compile(r"""^\s*(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)""")

# framework skip/focus markers only — bare ".skip(" / "xfail" match unrelated code
# (db.skip(5), a var named xfail) and cause false positives.
_SKIP_PATTERNS = (
    "@pytest.mark.skip", "@unittest.skip", "pytest.skip(", "@pytest.mark.xfail",
    "it.skip(", "describe.skip(", "test.skip(", "context.skip(",
    "it.only(", "describe.only(",
)


def _test_fn_names(lines: list[str], language: str) -> set[str]:
    pat = _PY_TEST_FN if language == "python" else _JS_TEST_FN if language in ("js", "ts") else None
    if pat is None:
        return set()
    return {m.group(1) for ln in lines if (m := pat.match(ln))}


def check(diff: Diff, root: str) -> list[Finding]:
    out: list[Finding] = []
    for f in diff.files:
        if not f.is_test:
            continue

        if f.status == "D":
            out.append(Finding("deleted-test", Severity.BLOCK,
                               f"test file deleted: {f.path}", file=f.path))
            continue  # nothing else to say about a gone file
        if f.status == "R":
            out.append(Finding("renamed-test", Severity.WARN,
                               f"test file renamed: {f.old_path} -> {f.path}",
                               file=f.path))
            if _is_test(f.old_path) and not _is_test(f.path):
                out.append(Finding("test-path-removed", Severity.WARN,
                                   "test renamed outside recognized test paths; "
                                   "verify discovery/configuration for the new path",
                                   file=f.path, evidence=f"{f.old_path} -> {f.path}"))

        # Copies leave the source test intact; edits with the same name are not removals.
        gone = (set() if f.status == "C" else
                _test_fn_names(f.removed, f.old_language or f.language)
                - _test_fn_names(f.added, f.language))
        if gone:
            out.append(Finding("removed-test-fn", Severity.WARN,
                               f"test function(s) removed: {', '.join(sorted(gone))}",
                               file=f.path))

        for ln in f.added:
            if any(p in ln for p in _SKIP_PATTERNS):
                out.append(Finding("added-skip", Severity.WARN,
                                   "a skip/only marker was added to a test", file=f.path,
                                   evidence=ln.strip()))
                break
    return out
