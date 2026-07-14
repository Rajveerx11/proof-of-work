"""Test-suite integrity: deleted/renamed test files, removed test fns, added skips."""
from __future__ import annotations

import re

from ...types import Diff, Finding, Severity

_PY_TEST_FN = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_TEST_FN = re.compile(r"""^\s*(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)""")

_SKIP_PATTERNS = (
    "@pytest.mark.skip", "@unittest.skip", "pytest.skip(", "xfail",
    ".skip(", "it.only(", "describe.only(",
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

        # only flag test fns that are gone, not ones edited in place (same name re-added)
        gone = _test_fn_names(f.removed, f.language) - _test_fn_names(f.added, f.language)
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
