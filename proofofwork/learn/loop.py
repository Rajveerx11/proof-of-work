"""Orchestrate one pass of the loop: for each frozen cheat the current ruleset misses, draft
a rule, gate it hard, and (if it passes) promote it into the learned ruleset.

ADD-ONLY and idempotent: a second run finds every promoted cheat "already caught" and does
nothing. Every promotion is a git-tracked edit to learned.json — rollback is `git revert`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..core.detector import ALL_CHECKS
from ..core.detector import learned as _learned
from ..rules import RULES_FILE
from ..types import Diff, Severity
from . import corpus
from . import propose as _propose
from .gate import evaluate


@dataclass
class LoopResult:
    promoted: list[dict] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (cheat name, why)


def _current_findings(diff: Diff, rules_path: str):
    findings = []
    for fn in ALL_CHECKS:
        try:
            findings.extend(fn(diff, "."))
        except Exception:  # a broken built-in must not derail mining
            pass
    findings.extend(_learned.apply_rules(diff, _learned.load_rules(rules_path)))
    return findings


def _already_caught(diff: Diff, rules_path: str) -> bool:
    return any(f.severity in (Severity.BLOCK, Severity.WARN)
               for f in _current_findings(diff, rules_path))


def run(rules_path: str | None = None, *, write: bool = True) -> LoopResult:
    path = rules_path or RULES_FILE
    clean = [d for _, d in corpus.clean()]
    res = LoopResult()
    known_ids = {r["id"] for r in _learned.load_rules(path)}

    for name, cheat in corpus.cheats():
        if _already_caught(cheat, path):
            res.skipped.append((name, "already caught"))
            continue
        rule = _propose.propose(name, cheat, clean)
        if rule is None:
            res.skipped.append((name, "no candidate pattern"))
            continue
        if rule["id"] in known_ids or rule["id"] in {r["id"] for r in res.promoted}:
            res.skipped.append((name, "duplicate rule id"))
            continue
        verdict = evaluate(rule, cheat, clean)
        if not verdict.promote:
            res.skipped.append((name, verdict.reason))
            continue
        rule["provenance"] = verdict.reason
        res.promoted.append(rule)

    if write and res.promoted:
        _write(path, res.promoted)
    return res


def _write(path: str, rules: list[dict]) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"ruleset_version": 1, "rules": []}
    data.setdefault("rules", []).extend(rules)
    data["ruleset_version"] = data.get("ruleset_version", 1) + 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
