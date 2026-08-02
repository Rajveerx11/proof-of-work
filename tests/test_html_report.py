from __future__ import annotations

from proofofwork.eval import build_report, record_run, render_html
from proofofwork.eval.harness import EvalResult, ProcessResult, UsageMetrics
from proofofwork.types import Finding, Severity, Verdict


def _result(
    task_id: str,
    *,
    passed: bool,
    category: str,
    agent_label: str,
    model_label: str | None,
    usage: UsageMetrics | None,
    secret_output: str = "",
) -> EvalResult:
    agent = ProcessResult(0 if passed else 7, 1.25, False, secret_output, secret_output)
    outcome = ProcessResult(0 if passed else 1, 0.5, False, "", "")
    gate = Verdict(
        passed=passed,
        reasons=["no cheat signals" if passed else "BLOCK <unsafe> & changed verifier"],
        findings=(
            []
            if passed
            else [Finding("eval:test", Severity.BLOCK, "unsafe", "verify.py", 1)]
        ),
    )
    return EvalResult(
        task_id,
        agent,
        outcome,
        passed,
        gate=gate,
        usage=usage,
        agent_label=agent_label,
        model_label=model_label,
        category=category,
        difficulty="medium",
        corpus_version="0.2.0",
    )


def test_html_report_is_deterministic_sorted_escaped_and_contains_required_metrics(tmp_path):
    db = tmp_path / "history.db"
    secret = "stdout-secret-must-not-persist"
    record_run(
        _result(
            "z-task<script>",
            passed=False,
            category="regression<script>",
            agent_label="Agent <red>",
            model_label="model&one",
            usage=None,
            secret_output=secret,
        ),
        db,
    )
    record_run(
        _result(
            "a-task",
            passed=True,
            category="bug-fix",
            agent_label="Agent A",
            model_label="model-two",
            usage=UsageMetrics(1200, 300, 12_345),
        ),
        db,
    )

    report = build_report(db)
    first = render_html(report)
    second = render_html(report)

    assert first == second
    assert first.index("a-task") < first.index("z-task")
    assert "z-task&lt;script&gt;" in first
    assert "Agent &lt;red&gt;" in first
    assert "model&amp;one" in first
    assert "regression&lt;script&gt;" in first
    assert "BLOCK eval:test" in first
    assert "regression<script>" not in first
    assert "z-task<script>" not in first
    assert "$0.012345" in first
    assert "1200" in first and "300" in first
    assert "1/2 (50.0%)" in first
    assert "1.750 s" in first
    assert "Comparison against previous runs" in first
    assert "Latest 1 run(s) versus previous 1 run(s)." in first
    assert "Corpus version: 0.2.0" in first
    assert secret not in first
    for file in tmp_path.glob("history.db*"):
        assert secret.encode() not in file.read_bytes()


def test_html_report_distinguishes_unknown_usage_from_reported_zero(tmp_path):
    db = tmp_path / "history.db"
    record_run(
        _result(
            "unknown-usage",
            passed=True,
            category="bug-fix",
            agent_label="generic",
            model_label=None,
            usage=None,
        ),
        db,
    )
    record_run(
        _result(
            "zero-usage",
            passed=True,
            category="feature",
            agent_label="generic",
            model_label=None,
            usage=UsageMetrics(0, 0, 0),
        ),
        db,
    )

    document = render_html(build_report(db))

    unknown_row = document[document.index("unknown-usage") : document.index("</tr>", document.index("unknown-usage"))]
    zero_row = document[document.index("zero-usage") : document.index("</tr>", document.index("zero-usage"))]
    assert "unknown" in unknown_row
    assert "$0.000000" in zero_row


def test_empty_html_report_contains_no_invented_comparison(tmp_path):
    document = render_html(build_report(tmp_path / "missing.db"))

    assert "No runs recorded." in document
    assert "Need at least two runs for equal-window comparison." in document
    assert "Latest" not in document
