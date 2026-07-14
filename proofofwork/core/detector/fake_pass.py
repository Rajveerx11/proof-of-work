"""Fake-pass signals: hard exits that short-circuit a test run, coverage kills."""
from __future__ import annotations

import re

from ...types import Diff, Finding, Severity

# sys.exit(0) / sys.exit() / os._exit( / pytest.exit(  — sys.exit(1) is a real failure, ignore it
_PY_EXIT = re.compile(r"\b(?:sys\.exit\(\s*(?:0\s*)?\)|os\._exit\(|pytest\.exit\()")
# only bare process.exit() / process.exit(0) — process.exit(1) is a real failure signal
_JS_EXIT = re.compile(r"\bprocess\.exit\(\s*(?:0\s*)?\)")
_PRAGMA = "# pragma: no cover"


def check(diff: Diff, root: str) -> list[Finding]:
    out: list[Finding] = []
    for f in diff.files:
        pragma_hits = 0
        for ln in f.added:
            if _PRAGMA in ln:
                pragma_hits += 1
            if "--no-cov" in ln:  # the pytest-cov disable flag; bare "no_cov" is too broad
                out.append(Finding("fake-pass:coverage-disabled", Severity.WARN,
                                   "coverage appears to be disabled", file=f.path,
                                   evidence=ln.strip()))
            if not f.is_test:
                continue
            if _PY_EXIT.search(ln):
                out.append(Finding("fake-pass:sys-exit", Severity.BLOCK,
                                   "hard exit added in a test — passes without running",
                                   file=f.path, evidence=ln.strip()))
            if f.language in ("js", "ts") and _JS_EXIT.search(ln):
                out.append(Finding("fake-pass:process-exit", Severity.BLOCK,
                                   "process.exit added in a test — short-circuits the run",
                                   file=f.path, evidence=ln.strip()))
        if pragma_hits >= 3:  # a spree, not a stray legit exclusion
            out.append(Finding("fake-pass:coverage-disabled", Severity.WARN,
                               f"{pragma_hits} '# pragma: no cover' added", file=f.path))
    return out

# ponytail: patched-runner detection (a conftest that fakes pytest exit codes) skipped —
# too repo-specific for v1. Add a conftest.py/setup.cfg content scan when a real case shows up.
