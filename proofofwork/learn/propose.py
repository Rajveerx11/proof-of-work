"""Draft a candidate rule from a missed cheat — deterministic, no LLM required.

Heuristic: pick the most cheat-like added line the clean corpus never contains, then turn its
distinctive head (the signature before any call args) into a whitespace-tolerant regex. This
makes the loop self-improving even offline. An LLM drafter is a drop-in alternative — it would
produce the same rule shape and face the exact same gate, so it gains no authority.
"""
from __future__ import annotations

import re

from ..types import Diff

# tokens that mark the usual lazy-agent cheats (skip a suite, fake a pass, stub the unit)
_CHEAT_TOKENS = ("skip", "xfail", "exit", "mock", "patch", "monkeypatch", "pragma",
                 "no cover", "no-cov", "disable", "return", "pass", "todo", "true")


def _clean_added(clean: list[Diff]) -> set[str]:
    return {ln.strip() for d in clean for f in d.files for ln in f.added if ln.strip()}


def _pattern_source(line: str) -> str:
    """Distinctive head of a line: the call/assignment signature before its args.

    'pytestmark = pytest.mark.skip(reason="x")' -> 'pytestmark = pytest.mark.skip'
    so the rule generalizes across args instead of pinning to one literal.
    """
    s = line.strip()
    head = s.split("(", 1)[0]
    if "(" in s and ("." in head or "=" in head) and len(head.split()) <= 6:
        return head
    return s


def _to_regex(line: str) -> str:
    return r"\s+".join(re.escape(t) for t in _pattern_source(line).split())


def _score(line: str) -> int:
    low = line.lower()
    return sum(1 for t in _CHEAT_TOKENS if t in low)


def propose(name: str, target: Diff, clean: list[Diff]) -> dict | None:
    """Return a candidate rule for `target` (a missed cheat), or None if none stands out."""
    banned = _clean_added(clean)
    candidates: list[tuple[object, str]] = []
    for f in target.files:
        for ln in f.added:
            s = ln.strip()
            if s and s not in banned:
                candidates.append((f, s))
    if not candidates:
        return None
    # most cheat-like line wins; tiebreak on length (a longer line is more specific)
    f, line = max(candidates, key=lambda c: (_score(c[1]), len(c[1])))
    slug = re.sub(r"[^a-z0-9]+", "-", _pattern_source(line).lower()).strip("-")[:48] or "pattern"
    return {
        "id": f"learned:{slug}",
        "version": 1,
        "pattern": _to_regex(line),
        "severity": "warn",
        "message": f"matches a cheat pattern learned from {name}",
        "languages": [f.language] if f.language else [],
        "test_only": f.is_test,
        "source": f"corpus:cheats/{name}",
    }
