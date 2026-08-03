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


def test_eval_run_json(monkeypatch, capsys):
    import proofofwork.eval as eval_module
    from proofofwork.eval.harness import EvalResult, ProcessResult, UsageMetrics

    process = ProcessResult(0, 0.1, False, "", "")
    gate = _verdict(True)
    recorded = {}
    monkeypatch.setattr(eval_module, "load_task", lambda *a: object())
    monkeypatch.setattr(
        eval_module,
        "run_task",
        lambda *a, **k: EvalResult(
            "task-1",
            process,
            process,
            True,
            gate=gate,
            usage=UsageMetrics(1200, 300, 12_345),
        ),
    )
    monkeypatch.setattr(
        eval_module,
        "record_run",
        lambda result, db: recorded.update(result=result, db=db) or 7,
    )
    assert cli.main([
        "eval",
        "run",
        "task.yaml",
        "--agent-argv-json",
        '["agent", "{workspace}"]',
        "--db",
        "history.db",
        "--json",
    ]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task_id"] == "task-1"
    assert data["gate"]["passed"] is True
    assert data["usage"] == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500,
        "cost_usd": 0.012345,
    }
    assert data["history"] == {
        "recorded": True,
        "database": "history.db",
        "run_id": 7,
    }
    assert recorded["db"] == "history.db"


def test_eval_run_human_output_reports_usage(monkeypatch, capsys):
    import proofofwork.eval as eval_module
    from proofofwork.eval.harness import EvalResult, ProcessResult, UsageMetrics

    process = ProcessResult(0, 0.1, False, "", "")
    monkeypatch.setattr(eval_module, "load_task", lambda *a: object())
    monkeypatch.setattr(
        eval_module,
        "run_task",
        lambda *a, **k: EvalResult(
            "task-1",
            process,
            process,
            True,
            gate=_verdict(True),
            usage=UsageMetrics(10, 5, 100),
        ),
    )

    assert cli.main([
        "eval",
        "run",
        "task.yaml",
        "--agent-argv-json",
        '["agent", "{workspace}"]',
        "--no-record",
    ]) == 0
    assert "usage: 15 tokens (input=10, output=5), $0.000100 USD" in capsys.readouterr().out


def test_eval_run_can_skip_history(monkeypatch, capsys):
    import proofofwork.eval as eval_module
    from proofofwork.eval.harness import EvalResult, ProcessResult

    process = ProcessResult(0, 0.1, False, "", "")
    monkeypatch.setattr(eval_module, "load_task", lambda *a: object())
    monkeypatch.setattr(
        eval_module,
        "run_task",
        lambda *a, **k: EvalResult("task-1", process, process, True, gate=_verdict(True)),
    )
    monkeypatch.setattr(
        eval_module,
        "record_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not record")),
    )

    assert cli.main([
        "eval",
        "run",
        "task.yaml",
        "--agent-argv-json",
        '["agent", "{workspace}"]',
        "--no-record",
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["history"] == {"recorded": False}


def test_eval_report_json(tmp_path, capsys):
    from proofofwork.eval import record_run
    from proofofwork.eval.harness import EvalResult, ProcessResult

    db = tmp_path / "history.db"
    process = ProcessResult(0, 0.1, False, "", "")
    record_run(EvalResult("task-1", process, process, True, gate=_verdict(True)), db)

    assert cli.main([
        "eval",
        "report",
        "--db",
        str(db),
        "--task-id",
        "task-1",
        "--json",
    ]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["runs"] == 1
    assert data["runs"][0]["task_id"] == "task-1"


def test_eval_report_json_writes_file(tmp_path, capsys):
    from proofofwork.eval import record_run
    from proofofwork.eval.harness import EvalResult, ProcessResult

    db = tmp_path / "history.db"
    output = tmp_path / "report.json"
    process = ProcessResult(0, 0.1, False, "", "")
    record_run(EvalResult("task-1", process, process, True, gate=_verdict(True)), db)

    assert cli.main([
        "eval",
        "report",
        "--db",
        str(db),
        "--format",
        "json",
        "--output",
        str(output),
    ]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["totals"]["runs"] == 1
    assert f"JSON report written to {output}" in capsys.readouterr().out


def test_eval_report_can_omit_noncomparable_trend(tmp_path, capsys):
    from proofofwork.eval import record_run
    from proofofwork.eval.harness import EvalResult, ProcessResult

    db = tmp_path / "history.db"
    process = ProcessResult(0, 0.1, False, "", "")
    record_run(EvalResult("task-1", process, process, True, gate=_verdict(True)), db)
    record_run(EvalResult("task-2", process, process, True, gate=_verdict(True)), db)

    assert cli.main([
        "eval",
        "report",
        "--db",
        str(db),
        "--format",
        "json",
        "--no-comparison",
    ]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["comparison_included"] is False
    assert data["trend"] is None


def test_eval_report_html_can_omit_noncomparable_trend(tmp_path, capsys):
    from proofofwork.eval import record_run
    from proofofwork.eval.harness import EvalResult, ProcessResult

    db = tmp_path / "history.db"
    process = ProcessResult(0, 0.1, False, "", "")
    record_run(EvalResult("task-1", process, process, True, gate=_verdict(True)), db)
    record_run(EvalResult("task-2", process, process, True, gate=_verdict(True)), db)

    assert cli.main([
        "eval",
        "report",
        "--db",
        str(db),
        "--format",
        "html",
        "--no-comparison",
    ]) == 0
    assert "Comparison against previous runs" not in capsys.readouterr().out


def test_eval_report_html_writes_static_file(tmp_path, capsys):
    from proofofwork.eval import record_run
    from proofofwork.eval.harness import EvalResult, ProcessResult

    db = tmp_path / "history.db"
    output = tmp_path / "report.html"
    process = ProcessResult(0, 0.1, False, "", "")
    record_run(
        EvalResult(
            "task-1",
            process,
            process,
            True,
            gate=_verdict(True),
            agent_label="Codex",
            model_label="model-1",
            category="bug-fix",
            difficulty="easy",
            corpus_version="0.2.0",
        ),
        db,
    )

    assert cli.main([
        "eval",
        "report",
        "--db",
        str(db),
        "--format",
        "html",
        "--output",
        str(output),
    ]) == 0
    document = output.read_text(encoding="utf-8")
    assert "Agent evaluation report" in document
    assert "Codex" in document
    assert "model-1" in document
    assert str(db) not in document
    assert f"HTML report written to {output}" in capsys.readouterr().out


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
