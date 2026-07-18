"""The self-improving loop end to end: gate rejects false positives, the loop promotes a rule
for a missed cheat, and the detector then catches that cheat. Plus corpus/patch parsing.
"""
from __future__ import annotations

import json

from proofofwork.core.detector import learned
from proofofwork.core.gitdiff import parse_patch
from proofofwork.learn import corpus, loop, propose
from proofofwork.learn.gate import evaluate
from proofofwork.types import Severity


def _fires(rule, diff):
    return bool(learned.apply_rules(diff, [rule]))


# --- patch parsing ---

def test_parse_patch_status_and_flags():
    d = parse_patch(
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
        "@@ -1 +1,2 @@\n def test_x():\n+    sys.exit(0)\n"
    )
    f = d.files[0]
    assert f.path == "tests/test_x.py"
    assert f.status == "M" and f.is_test and f.language == "python"
    assert "    sys.exit(0)" in f.added


def test_parse_patch_add_and_delete_status():
    added = parse_patch(
        "diff --git a/n.py b/n.py\n--- /dev/null\n+++ b/n.py\n@@ -0,0 +1 @@\n+x = 1\n")
    deleted = parse_patch(
        "diff --git a/o.py b/o.py\n--- a/o.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n")
    assert added.files[0].status == "A"
    assert deleted.files[0].status == "D"


# --- gate ---

def test_gate_rejects_false_positive():
    # a rule matching a line the clean corpus contains must never promote
    rule = {"id": "learned:return", "pattern": r"return", "severity": "warn"}
    target = parse_patch(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+    return 0\n")
    clean = [d for _, d in corpus.clean()]
    res = evaluate(rule, target, clean)
    assert res.promote is False
    assert res.false_positives > 0


def test_gate_accepts_clean_specific_catch():
    _, cheat = corpus.cheats()[0]  # module_skip
    clean = [d for _, d in corpus.clean()]
    rule = propose.propose("module_skip.diff", cheat, clean)
    res = evaluate(rule, cheat, clean)
    assert res.promote is True
    assert res.caught == 1 and res.false_positives == 0


# --- full loop ---

def test_loop_promotes_missed_and_skips_caught(tmp_path):
    rules_file = tmp_path / "learned.json"
    rules_file.write_text(json.dumps({"ruleset_version": 1, "rules": []}))

    res = loop.run(rules_path=str(rules_file), write=True)
    skipped = dict(res.skipped)

    # the module-level skip is missed by the built-ins -> a rule is learned for it
    assert any("module_skip" in r["source"] for r in res.promoted)
    # sys.exit(0) is already caught by fake_pass -> nothing new learned for it
    assert skipped.get("sys_exit_fake.diff") == "already caught"

    # the promoted rule is now live: the detector catches the once-missed cheat
    rules = learned.load_rules(str(rules_file))
    _, cheat = next(c for c in corpus.cheats() if c[0] == "module_skip.diff")
    findings = learned.apply_rules(cheat, rules)
    assert any(f.severity in (Severity.BLOCK, Severity.WARN) for f in findings)


def test_loop_is_idempotent(tmp_path):
    rules_file = tmp_path / "learned.json"
    rules_file.write_text(json.dumps({"ruleset_version": 1, "rules": []}))

    first = loop.run(rules_path=str(rules_file), write=True)
    assert first.promoted
    second = loop.run(rules_path=str(rules_file), write=True)
    assert second.promoted == []  # add-only: a second pass learns nothing new
    assert all(why == "already caught" for _, why in second.skipped)
