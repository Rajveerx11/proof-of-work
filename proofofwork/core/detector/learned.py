"""Learned-rules layer: data-driven cheat patterns the self-improving loop promotes.

Declarative regex rules live in proofofwork/rules/learned.json. They run alongside the four
built-in checks but are invoked separately by the engine (like coverage_delta) so ALL_CHECKS
stays the fixed built-in set. Add-only and fail-open: a broken/absent rule never crashes the
gate — it is simply skipped.
"""
from __future__ import annotations

import json
import re

from ...rules import RULES_FILE
from ...types import Diff, Finding, Severity

_SEV = {"block": Severity.BLOCK, "warn": Severity.WARN, "info": Severity.INFO}


def load_rules(path: str | None = None) -> list[dict]:
    try:
        with open(path or RULES_FILE, encoding="utf-8") as f:
            return json.load(f).get("rules", [])
    except (OSError, ValueError):
        return []


def apply_rules(diff: Diff, rules: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    for rule in rules:
        try:
            pat = re.compile(rule["pattern"])
        except (re.error, KeyError, TypeError):
            continue  # a malformed rule is skipped, never fatal
        sev = _SEV.get(rule.get("severity", "warn"), Severity.WARN)
        langs = rule.get("languages") or []
        test_only = rule.get("test_only", False)
        for f in diff.files:
            if test_only and not f.is_test:
                continue
            if langs and f.language not in langs:
                continue
            for ln in f.added:
                if pat.search(ln):
                    out.append(Finding(rule["id"], sev,
                                       rule.get("message", "matches a learned cheat pattern"),
                                       file=f.path, evidence=ln.strip()))
                    break  # one flag per file — heuristic net, don't spam
    return out


def check(diff: Diff, root: str) -> list[Finding]:
    return apply_rules(diff, load_rules())
