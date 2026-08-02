from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from proofofwork.eval import EvalResult, UsageMetrics
from proofofwork.eval.harness import ProcessResult
from proofofwork.eval.history import HistoryError, build_report, record_run
from proofofwork.types import Finding, Severity, Verdict


def _result(
    task_id: str,
    passed: bool,
    *,
    agent_seconds: float = 1.0,
    outcome_seconds: float = 0.25,
    secret_output: str = "",
    usage: UsageMetrics | None = None,
) -> EvalResult:
    process = ProcessResult(0, agent_seconds, False, secret_output, secret_output)
    outcome = ProcessResult(0 if passed else 1, outcome_seconds, False, "", "")
    gate = Verdict(
        passed=passed,
        reasons=["no cheat signals" if passed else "BLOCK deleted-test"],
        findings=(
            []
            if passed
            else [Finding("deleted-test", Severity.BLOCK, "removed a test", "test_app.py", 1)]
        ),
    )
    return EvalResult(task_id, process, outcome, passed, gate=gate, usage=usage)


def test_records_history_and_builds_equal_window_trend(tmp_path):
    db = tmp_path / "history.db"
    started = datetime(2026, 7, 1, tzinfo=UTC)
    results = [
        _result("task-1", False, agent_seconds=4.0),
        _result("task-1", False, agent_seconds=3.0),
        _result("task-1", True, agent_seconds=2.0),
        _result("task-1", True, agent_seconds=1.0),
    ]

    ids = [
        record_run(result, db, recorded_at=started + timedelta(days=index))
        for index, result in enumerate(results)
    ]
    report = build_report(db, window=2, limit=3)

    assert ids == [1, 2, 3, 4]
    assert report.totals.runs == 4
    assert report.totals.passed == 2
    assert report.totals.pass_rate == 0.5
    assert report.trend.window_size == 2
    assert report.trend.recent.pass_rate == 1.0
    assert report.trend.previous.pass_rate == 0.0
    assert report.trend.pass_rate_delta_percentage_points == 100.0
    assert report.trend.average_agent_duration_delta_seconds == -2.0
    assert [run.id for run in report.runs] == [4, 3, 2]
    assert report.runs[0].gate["passed"] is True


def test_usage_metrics_are_persisted_and_aggregated_without_counting_unknown_as_zero(tmp_path):
    db = tmp_path / "history.db"
    record_run(
        _result("task-1", True, usage=UsageMetrics(1200, 300, 12_345)),
        db,
    )
    record_run(_result("task-1", True), db)
    record_run(
        _result("task-1", False, usage=UsageMetrics(800, 200, 7_655)),
        db,
    )

    report = build_report(db)
    usage = report.totals.as_dict()["usage"]

    assert usage == {
        "runs": 2,
        "total_input_tokens": 2000,
        "total_output_tokens": 500,
        "total_tokens": 2500,
        "total_cost_usd": 0.02,
        "average_tokens_per_run": 1250.0,
        "average_cost_usd_per_run": 0.01,
    }
    assert report.runs[0].as_dict()["usage"]["cost_usd"] == 0.007655
    assert report.runs[1].as_dict()["usage"] is None


def test_usage_totals_do_not_overflow_sqlite_integer_aggregates(tmp_path):
    db = tmp_path / "history.db"
    large = 2**62
    record_run(_result("task-1", True, usage=UsageMetrics(large, 0, 1)), db)
    record_run(_result("task-1", True, usage=UsageMetrics(large, 0, 1)), db)

    report = build_report(db)

    assert report.totals.total_input_tokens == 2**63
    assert report.totals.total_tokens == 2**63


def test_legacy_history_is_migrated_without_losing_runs(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE eval_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                task_id TEXT NOT NULL,
                passed INTEGER NOT NULL,
                agent_exit_code INTEGER,
                agent_duration_seconds REAL NOT NULL,
                agent_timed_out INTEGER NOT NULL,
                outcome_exit_code INTEGER,
                outcome_duration_seconds REAL NOT NULL,
                outcome_timed_out INTEGER NOT NULL,
                gate_passed INTEGER,
                verification TEXT NOT NULL,
                gate_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO eval_runs(
                recorded_at, task_id, passed,
                agent_exit_code, agent_duration_seconds, agent_timed_out,
                outcome_exit_code, outcome_duration_seconds, outcome_timed_out,
                gate_passed, verification, gate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                "legacy-task",
                1,
                0,
                1.0,
                0,
                0,
                0.25,
                0,
                1,
                "outcome-command+proof-of-work-gate",
                "{}",
            ),
        )

    report = build_report(db)
    new_id = record_run(
        _result("new-task", True, usage=UsageMetrics(10, 5, 100)),
        db,
    )

    assert report.totals.runs == 1
    assert report.totals.usage_runs == 0
    assert report.runs[0].as_dict()["usage"] is None
    assert new_id == 2
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(eval_runs)")}
    assert {"input_tokens", "output_tokens", "cost_usd_micros"} <= columns


def test_report_filters_one_task_without_affecting_all_task_totals(tmp_path):
    db = tmp_path / "history.db"
    record_run(_result("task-a", True), db)
    record_run(_result("task-b", False), db)
    record_run(_result("task-a", True), db)

    task_report = build_report(db, task_id="task-a")
    all_report = build_report(db)

    assert task_report.totals.runs == 2
    assert task_report.totals.passed == 2
    assert {run.task_id for run in task_report.runs} == {"task-a"}
    assert all_report.totals.runs == 3


def test_history_does_not_persist_process_output(tmp_path):
    db = tmp_path / "history.db"
    secret = "agent-output-secret-5ce379"

    record_run(_result("task-1", True, secret_output=secret), db)
    report = build_report(db)

    assert secret not in str(report.as_dict())
    for file in tmp_path.glob("history.db*"):
        assert secret.encode() not in file.read_bytes()


def test_report_closes_database_handles_on_windows(tmp_path):
    db = tmp_path / "history.db"
    record_run(_result("task-1", True), db)

    assert build_report(db).totals.runs == 1

    for file in tmp_path.glob("history.db*"):
        file.unlink()
    assert not list(tmp_path.glob("history.db*"))


def test_missing_or_unrelated_database_reports_empty(tmp_path):
    missing = tmp_path / "missing.db"

    assert build_report(missing).totals.runs == 0
    assert not missing.exists()

    unrelated = tmp_path / "unrelated.db"
    with sqlite3.connect(unrelated) as conn:
        conn.execute("CREATE TABLE other(value TEXT)")
    assert build_report(unrelated).totals.runs == 0


@pytest.mark.parametrize("gate_json", ["{broken", "[]", '"string"', "null"])
def test_corrupt_gate_data_fails_closed(tmp_path, gate_json):
    db = tmp_path / "history.db"
    record_run(_result("task-1", True), db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE eval_runs SET gate_json = ? WHERE id = 1", (gate_json,))

    with pytest.raises(HistoryError, match="invalid gate data"):
        build_report(db)


@pytest.mark.parametrize(("name", "value"), [("window", 0), ("limit", 1001), ("limit", True)])
def test_report_bounds_are_validated(tmp_path, name, value):
    arguments = {name: value}

    with pytest.raises(ValueError, match=name):
        build_report(tmp_path / "history.db", **arguments)


def test_concurrent_writers_receive_unique_run_ids(tmp_path):
    db = tmp_path / "history.db"

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: record_run(_result("task-1", True), db), range(8)))

    assert len(set(ids)) == 8
    assert build_report(db).totals.runs == 8


def test_report_uses_one_snapshot_during_concurrent_write(tmp_path, monkeypatch):
    from proofofwork.eval import history

    db = tmp_path / "history.db"
    record_run(_result("task-1", True), db)
    original_connect = sqlite3.connect
    inserted = threading.Event()
    writer_errors = []

    def insert_during_report():
        try:
            with original_connect(db) as writer:
                writer.execute("PRAGMA busy_timeout=5000;")
                writer.execute(
                    """
                    INSERT INTO eval_runs(
                        recorded_at, task_id, passed,
                        agent_exit_code, agent_duration_seconds, agent_timed_out,
                        outcome_exit_code, outcome_duration_seconds, outcome_timed_out,
                        gate_passed, verification, gate_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now(UTC).isoformat(),
                        "task-1",
                        1,
                        0,
                        1.0,
                        0,
                        0,
                        0.25,
                        0,
                        1,
                        "outcome-command+proof-of-work-gate",
                        "{}",
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - surfaced in the test thread
            writer_errors.append(exc)

    class InterleavingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            cursor = super().execute(sql, parameters)
            if "COUNT(*) AS runs" in sql and not inserted.is_set():
                inserted.set()
                writer = threading.Thread(target=insert_during_report)
                writer.start()
                writer.join(timeout=10)
                assert not writer.is_alive()
            return cursor

    def interleaving_connect(database, *args, **kwargs):
        return original_connect(
            database,
            *args,
            factory=InterleavingConnection,
            **kwargs,
        )

    monkeypatch.setattr(history.sqlite3, "connect", interleaving_connect)

    report = build_report(db)

    assert not writer_errors
    assert inserted.is_set()
    assert report.totals.runs == 1
    assert [run.id for run in report.runs] == [1]
    monkeypatch.undo()
    assert build_report(db).totals.runs == 2
