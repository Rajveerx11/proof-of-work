"""The hard safety gate. A candidate rule is promoted ONLY if it catches a real cheat the
current ruleset misses AND fires on zero clean-corpus diffs. The drafter (heuristic or LLM)
has no authority to ship — this gate decides.

# ponytail: hard 0-FP-on-clean-corpus, not a Wilson lower bound. On a small frozen corpus a
# single false positive is disqualifying — stricter and simpler than a statistical rate.
# Upgrade path (plan §4): Wilson LB per rule + aggregate FP budget once the clean corpus is
# large enough for a rate to mean anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.detector.learned import apply_rules
from ..types import Diff


@dataclass
class GateResult:
    promote: bool
    caught: int           # cheat diffs the candidate fires on (must be >= 1)
    false_positives: int  # clean diffs it wrongly fires on (must be 0)
    reason: str


def _fires(rule: dict, diff: Diff) -> bool:
    return bool(apply_rules(diff, [rule]))


def evaluate(rule: dict, target: Diff, clean: list[Diff]) -> GateResult:
    caught = 1 if _fires(rule, target) else 0
    fps = sum(1 for c in clean if _fires(rule, c))
    if caught < 1:
        return GateResult(False, caught, fps, "does not catch the target cheat")
    if fps > 0:
        return GateResult(False, caught, fps,
                          f"{fps} false positive(s) on the clean corpus")
    return GateResult(True, caught, fps,
                      "catches a missed cheat with zero false positives")
