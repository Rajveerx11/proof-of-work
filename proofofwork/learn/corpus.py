"""Frozen ground-truth corpora for the loop.

- cheats/: diffs that ARE cheats (a rule catching one is a true positive).
- clean/:  diffs that are legitimate (a rule firing on one is a false positive).

Labels are human/synthetic-anchored, NEVER LLM-labeled — that keeps the advisory judge out
of the signed verdict (plan §4). "Frozen" = checked into git; rollback is `git revert`.
"""
from __future__ import annotations

import os

from ..core.gitdiff import parse_patch
from ..types import Diff

_HERE = os.path.dirname(__file__)
CHEATS_DIR = os.path.join(_HERE, "corpora", "cheats")
CLEAN_DIR = os.path.join(_HERE, "corpora", "clean")


def _load_dir(path: str) -> list[tuple[str, Diff]]:
    out: list[tuple[str, Diff]] = []
    if not os.path.isdir(path):
        return out
    for name in sorted(os.listdir(path)):
        if not name.endswith(".diff"):
            continue
        with open(os.path.join(path, name), encoding="utf-8") as f:
            out.append((name, parse_patch(f.read())))
    return out


def cheats() -> list[tuple[str, Diff]]:
    return _load_dir(CHEATS_DIR)


def clean() -> list[tuple[str, Diff]]:
    return _load_dir(CLEAN_DIR)
