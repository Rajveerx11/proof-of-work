"""CLI contract: exit code mirrors verdict.passed; --json emits the verdict dict.
Engine is patched — real git repos / test runs are out of scope for a unit test."""
import json

from proofofwork import engine
from proofofwork.interfaces import cli
from proofofwork.learn.loop import LoopEvent, LoopResult
from proofofwork.types import Finding, Severity, Verdict


def _verdict(passed):
    return Verdict(
        passed=passed,
        reasons=["no cheat signals" if passed else "tests failed on a clean re-run"],
        findings=[] if passed else [Finding("deleted-test", Severity.BLOCK, "removed a test",
                                             file="t.py", line=3)],
    )


def test_check_pass(monkeypatch, capsys):
    monkeypatch.setattr(engine, "check", lambda *a, **k: _verdict(True))
    assert cli.main(["check", "--no-tests"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_fail(monkeypatch, capsys):
    monkeypatch.setattr(engine, "check", lambda *a, **k: _verdict(False))
    assert cli.main(["check", "--no-tests"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_json(monkeypatch, capsys):
    monkeypatch.setattr(engine, "check", lambda *a, **k: _verdict(True))
    assert cli.main(["check", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is True


def test_default_subcommand(monkeypatch):
    # no subcommand -> defaults to `check`
    monkeypatch.setattr(engine, "check", lambda *a, **k: _verdict(True))
    assert cli.main(["--no-tests"]) == 0


def test_learn_dry_run_json(monkeypatch, capsys):
    calls = {}

    def fake_run(*, rules_path, write):
        calls["rules_path"] = rules_path
        calls["write"] = write
        rule = {"id": "learned:pytestmark-pytest-mark-skip", "severity": "warn",
                "pattern": r"pytestmark\s+=\s+pytest\.mark\.skip"}
        return LoopResult(
            promoted=[rule],
            events=[LoopEvent("module_skip.diff", "promoted",
                              "catches a missed cheat with zero false positives",
                              rule=rule, caught=1, false_positives=0)],
        )

    from proofofwork.learn import loop
    monkeypatch.setattr(loop, "run", fake_run)

    assert cli.main(["learn", "--dry-run", "--json", "--rules", "rules.json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert calls == {"rules_path": "rules.json", "write": False}
    assert data["events"][0]["cheat"] == "module_skip.diff"
    assert data["events"][0]["false_positives"] == 0
