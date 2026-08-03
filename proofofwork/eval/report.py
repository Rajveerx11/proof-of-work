"""Deterministic, dependency-free static HTML evaluation reports."""
from __future__ import annotations

from html import escape

from .history import RunRecord, RunSummary, TrendReport


def render_html(report: TrendReport, *, include_comparison: bool = True) -> str:
    """Render escaped run summaries only; process output is not part of TrendReport."""
    runs = sorted(
        report.runs,
        key=lambda run: (
            run.category or "",
            run.task_id,
            run.agent_label or "",
            run.model_label or "",
            -run.id,
        ),
    )
    versions = sorted({run.corpus_version for run in runs if run.corpus_version})
    corpus_version = ", ".join(versions) if versions else "not recorded"
    totals = report.totals
    result_rows = "".join(_result_row(run) for run in runs)
    if not result_rows:
        result_rows = '<tr><td colspan="12" class="empty">No runs recorded.</td></tr>'

    comparison = (
        f"<h2>Comparison against previous runs</h2>{_comparison(report)}"
        if include_comparison
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
  <title>Proof of Work evaluation report</title>
  <style>
    :root {{ color-scheme: light; --ink:#17211b; --muted:#647067; --line:#d9e0db;
      --paper:#f7f8f5; --card:#fff; --accent:#166534; --fail:#991b1b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:14px/1.45 ui-sans-serif,system-ui,sans-serif; }}
    main {{ max-width:1400px; margin:auto; padding:40px 24px 64px; }}
    h1 {{ margin:0; font-size:32px; letter-spacing:-.03em; }}
    h2 {{ margin:36px 0 12px; font-size:20px; }}
    .eyebrow {{ color:var(--accent); font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
    .meta {{ color:var(--muted); margin-top:6px; }}
    .cards {{ display:grid; grid-template-columns:repeat(5,minmax(150px,1fr)); gap:12px; margin-top:24px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }}
    .card span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
    .card strong {{ display:block; margin-top:6px; font-size:22px; }}
    .table-wrap {{ overflow:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }}
    table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
    th,td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }}
    th {{ background:#eef2ed; color:#39443d; font-size:12px; letter-spacing:.04em; text-transform:uppercase; }}
    tr:last-child td {{ border-bottom:0; }}
    .pass {{ color:var(--accent); font-weight:700; }} .fail {{ color:var(--fail); font-weight:700; }}
    .reason {{ max-width:360px; white-space:normal; }} .empty {{ color:var(--muted); text-align:center; padding:28px; }}
    .note {{ color:var(--muted); margin:10px 0 0; }}
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} main {{ padding:24px 14px 40px; }} }}
  </style>
</head>
<body>
<main>
  <div class="eyebrow">Proof of Work</div>
  <h1>Agent evaluation report</h1>
  <div class="meta">Corpus version: {_e(corpus_version)}</div>
  <section class="cards" aria-label="Evaluation summary">
    {_card("Pass rate", _rate(totals.pass_rate))}
    {_card("Runs", str(totals.runs))}
    {_card("Wall time", _duration(totals.total_wall_time_seconds))}
    {_card("Reported exact cost", _summary_cost(totals))}
    {_card("Usage coverage", _coverage(totals))}
  </section>
  <h2>Task and category results</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Category</th><th>Task</th><th>Agent</th><th>Model</th><th>Result</th>
      <th>Failure reasons</th><th>Wall time</th><th>Input tokens</th><th>Output tokens</th>
      <th>Exact cost</th><th>Difficulty</th><th>Run</th></tr></thead>
      <tbody>{result_rows}</tbody>
    </table>
  </div>
  <p class="note">Unknown usage remains unknown; it is never counted as zero. Costs include only runs reporting exact USD micros.</p>
  {comparison}
</main>
</body>
</html>
"""


def _result_row(run: RunRecord) -> str:
    status = "PASS" if run.passed else "FAIL"
    status_class = "pass" if run.passed else "fail"
    reasons = "; ".join(run.failure_reasons) if run.failure_reasons else "—"
    return (
        "<tr>"
        f"<td>{_e(run.category or 'not recorded')}</td>"
        f"<td>{_e(run.task_id)}</td>"
        f"<td>{_e(run.agent_label or 'not recorded')}</td>"
        f"<td>{_e(run.model_label or 'not recorded')}</td>"
        f'<td class="{status_class}">{_e(status)}</td>'
        f'<td class="reason">{_e(reasons)}</td>'
        f"<td>{_e(_duration(run.wall_time_seconds))}</td>"
        f"<td>{_e(_known_integer(run.input_tokens))}</td>"
        f"<td>{_e(_known_integer(run.output_tokens))}</td>"
        f"<td>{_e(_cost(run.cost_usd_micros))}</td>"
        f"<td>{_e(run.difficulty or 'not recorded')}</td>"
        f"<td>#{_e(run.id)}</td>"
        "</tr>"
    )


def _comparison(report: TrendReport) -> str:
    trend = report.trend
    if trend.window_size == 0 or trend.recent is None or trend.previous is None:
        return '<p class="note">Need at least two runs for equal-window comparison.</p>'
    recent = trend.recent
    previous = trend.previous
    rows = (
        ("Pass rate", _rate(recent.pass_rate), _rate(previous.pass_rate), _points(trend.pass_rate_delta_percentage_points)),
        ("Average agent time", _optional_duration(recent.average_agent_duration_seconds), _optional_duration(previous.average_agent_duration_seconds), _signed_duration(trend.average_agent_duration_delta_seconds)),
        ("Average verifier time", _optional_duration(recent.average_outcome_duration_seconds), _optional_duration(previous.average_outcome_duration_seconds), _signed_duration(trend.average_outcome_duration_delta_seconds)),
        ("Reported tokens", str(recent.total_tokens) if recent.usage_runs else "unknown", str(previous.total_tokens) if previous.usage_runs else "unknown", "—"),
        ("Reported exact cost", _summary_cost(recent), _summary_cost(previous), "—"),
        ("Usage coverage", _coverage(recent), _coverage(previous), "—"),
    )
    body = "".join(
        f"<tr><td>{_e(metric)}</td><td>{_e(latest)}</td><td>{_e(earlier)}</td><td>{_e(change)}</td></tr>"
        for metric, latest, earlier, change in rows
    )
    size = _e(trend.window_size)
    return (
        f'<p class="note">Latest {size} run(s) versus previous {size} run(s).</p>'
        '<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Latest</th>'
        f"<th>Previous</th><th>Change</th></tr></thead><tbody>{body}</tbody></table></div>"
    )


def _card(label: str, value: str) -> str:
    return f'<div class="card"><span>{_e(label)}</span><strong>{_e(value)}</strong></div>'


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _rate(value: float | None) -> str:
    return "unknown" if value is None else f"{value * 100:.1f}%"


def _points(value: float | None) -> str:
    return "unknown" if value is None else f"{value:+.1f} pp"


def _duration(value: float) -> str:
    return f"{value:.3f} s"


def _optional_duration(value: float | None) -> str:
    return "unknown" if value is None else _duration(value)


def _signed_duration(value: float | None) -> str:
    return "unknown" if value is None else f"{value:+.3f} s"


def _known_integer(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _cost(micros: int | None) -> str:
    if micros is None:
        return "unknown"
    return f"${micros // 1_000_000}.{micros % 1_000_000:06d}"


def _summary_cost(summary: RunSummary) -> str:
    return _cost(summary.total_cost_usd_micros) if summary.usage_runs else "unknown"


def _coverage(summary: RunSummary) -> str:
    if not summary.runs:
        return "unknown"
    return f"{summary.usage_runs}/{summary.runs} ({summary.usage_runs / summary.runs * 100:.1f}%)"
