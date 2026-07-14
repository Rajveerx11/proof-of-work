"""Mock-under-test: a test mocks the very module being changed in the same diff.

Heuristic net, not proof — mocking a collaborator is normal; mocking the unit under
change is how logic gets stubbed away. We only flag the overlap.
"""
from __future__ import annotations

import os
import re

from ...types import Diff, Finding, Severity

_PATCH = re.compile(r"@patch\(|mock\.patch\(|jest\.mock\(|vi\.mock\(")
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


def _module_names(diff: Diff) -> set[str]:
    """Stems of non-test source modules touched (added/modified) in this diff."""
    names = set()
    for f in diff.files:
        if f.is_test or f.status == "D" or f.language not in ("python", "js", "ts"):
            continue
        stem = os.path.splitext(os.path.basename(f.path.replace("\\", "/")))[0]
        if stem and stem != "__init__":
            names.add(stem)
    return names


def check(diff: Diff, root: str) -> list[Finding]:
    targets = _module_names(diff)
    if not targets:
        return []

    out: list[Finding] = []
    for f in diff.files:
        if not f.is_test:
            continue
        for ln in f.added:
            if not _PATCH.search(ln):
                continue
            m = _QUOTED.search(ln)
            if not m:
                continue
            # split the mock target into path/dotted components and match against changed stems
            parts = re.split(r"[./\\]", m.group(1))
            hit = next((n for n in targets if n in parts), None)
            if hit:
                out.append(Finding("mock-under-test", Severity.WARN,
                                   f"test mocks '{hit}', a module changed in the same diff",
                                   file=f.path, evidence=ln.strip()))
                break  # ponytail: one flag per test file; heuristic, don't spam
    return out
