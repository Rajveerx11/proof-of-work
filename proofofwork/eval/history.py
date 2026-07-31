"""Persistent SQLite history and trend reports for coding-agent evaluations."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .harness import EvalResult

DEFAULT_HISTORY_DB = os.path.join(".proofofwork", "eval-runs.db")
MAX_REPORT_ROWS = 1_000


class HistoryError(RuntimeError):
    """Raised when eval history cannot be stored or read."""


@dataclass(frozen=True)
class RunRecord:
    id: int
    recorded_at: str
    task_id: str
    passed: bool
    agent_exit_code: int | None
    agent_duration_seconds: float
    agent_timed_out: bool
    outcome_exit_code: int | None
    outcome_duration_seconds: float
    outcome_timed_out: bool
    gate_passed: bool | None
    verification: str
    gate: dict | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "recorded_at": self.recorded_at,
            "task_id": self.task_id,
            "passed": self.passed,
            "agent": {
                "exit_code": self.agent_exit_code,
                "duration_seconds": self.agent_duration_seconds,
                "timed_out": self.agent_timed_out,
            },
            "outcome": {
                "exit_code": self.outcome_exit_code,
                "duration_seconds": self.outcome_duration_seconds,
                "timed_out": self.outcome_timed_out,
            },
            "gate_passed": self.gate_passed,
            "verification": self.verification,
            "gate": self.gate,
        }


@dataclass(frozen=True)
class RunSummary:
    runs: int
    passed: int
    failed: int
    pass_rate: float | None
    average_agent_duration_seconds: float | None
    average_outcome_duration_seconds: float | None

    def as_dict(self) -> dict:
        return {
            "runs": self.runs,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "average_agent_duration_seconds": self.average_agent_duration_seconds,
            "average_outcome_duration_seconds": self.average_outcome_duration_seconds,
        }


@dataclass(frozen=True)
class TrendComparison:
    window_size: int
    recent: RunSummary | None
    previous: RunSummary | None
    pass_rate_delta_percentage_points: float | None
    average_agent_duration_delta_seconds: float | None
    average_outcome_duration_delta_seconds: float | None

    def as_dict(self) -> dict:
        return {
            "window_size": self.window_size,
            "recent": self.recent.as_dict() if self.recent is not None else None,
            "previous": self.previous.as_dict() if self.previous is not None else None,
            "pass_rate_delta_percentage_points": self.pass_rate_delta_percentage_points,
            "average_agent_duration_delta_seconds": (
                self.average_agent_duration_delta_seconds
            ),
            "average_outcome_duration_delta_seconds": (
                self.average_outcome_duration_delta_seconds
            ),
        }


@dataclass(frozen=True)
class TrendReport:
    task_id: str | None
    totals: RunSummary
    trend: TrendComparison
    runs: tuple[RunRecord, ...]

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "totals": self.totals.as_dict(),
            "trend": self.trend.as_dict(),
            "runs": [run.as_dict() for run in self.runs],
        }


def record_run(
    result: EvalResult,
    db_path: str | Path = DEFAULT_HISTORY_DB,
    *,
    recorded_at: datetime | None = None,
) -> int:
    """Persist one eval summary and return its monotonic database id.

    Process output is deliberately excluded. Exit status, timing, timeout state,
    verification mode, and deterministic gate evidence are retained.
    """
    timestamp = recorded_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("recorded_at must include a timezone")
    gate = result.gate.as_dict() if result.gate is not None else None
    gate_json = (
        json.dumps(gate, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if gate is not None
        else None
    )
    conn = _connect_for_write(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            INSERT INTO eval_runs(
                recorded_at, task_id, passed,
                agent_exit_code, agent_duration_seconds, agent_timed_out,
                outcome_exit_code, outcome_duration_seconds, outcome_timed_out,
                gate_passed, verification, gate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.astimezone(UTC).isoformat(),
                result.task_id,
                int(result.passed),
                result.agent.exit_code,
                result.agent.duration_seconds,
                int(result.agent.timed_out),
                result.outcome.exit_code,
                result.outcome.duration_seconds,
                int(result.outcome.timed_out),
                int(result.gate.passed) if result.gate is not None else None,
                result.verification,
                gate_json,
            ),
        )
        conn.commit()
        if cursor.lastrowid is None:
            raise HistoryError("SQLite did not return an eval run id")
        return cursor.lastrowid
    except sqlite3.Error as exc:
        conn.rollback()
        raise HistoryError(f"cannot record eval history: {exc}") from exc
    finally:
        conn.close()


def build_report(
    db_path: str | Path = DEFAULT_HISTORY_DB,
    *,
    task_id: str | None = None,
    window: int = 10,
    limit: int = 20,
) -> TrendReport:
    """Read totals, equal recent/previous windows, and newest run records."""
    _validate_report_bound("window", window)
    _validate_report_bound("limit", limit)
    path = Path(db_path)
    if not path.exists():
        return _empty_report(task_id)

    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000;")
        # SQLite otherwise gives each SELECT its own snapshot. Hold one read
        # transaction so totals, trend windows, and listed runs cannot disagree
        # when another process records a result between queries.
        conn.execute("BEGIN")
        if not _history_table_exists(conn):
            return _empty_report(task_id)
        where, parameters = _task_filter(task_id)
        total_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS runs,
                COALESCE(SUM(passed), 0) AS passed,
                AVG(agent_duration_seconds) AS average_agent_duration_seconds,
                AVG(outcome_duration_seconds) AS average_outcome_duration_seconds
            FROM eval_runs
            {where}
            """,
            parameters,
        ).fetchone()
        comparison_rows = conn.execute(
            f"""
            SELECT *
            FROM eval_runs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*parameters, window * 2),
        ).fetchall()
        history_rows = conn.execute(
            f"""
            SELECT *
            FROM eval_runs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()
        conn.commit()
    except sqlite3.Error as exc:
        if "conn" in locals():
            conn.rollback()
        raise HistoryError(f"cannot read eval history: {exc}") from exc
    finally:
        if "conn" in locals():
            conn.close()

    totals = _summary_from_total(total_row)
    comparison = _comparison(comparison_rows, window)
    return TrendReport(
        task_id=task_id,
        totals=totals,
        trend=comparison,
        runs=tuple(_record_from_row(row) for row in history_rows),
    )


def _connect_for_write(db_path: str | Path) -> sqlite3.Connection:
    path = os.path.abspath(os.fspath(db_path))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                task_id TEXT NOT NULL,
                passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                agent_exit_code INTEGER,
                agent_duration_seconds REAL NOT NULL
                    CHECK(agent_duration_seconds >= 0),
                agent_timed_out INTEGER NOT NULL
                    CHECK(agent_timed_out IN (0, 1)),
                outcome_exit_code INTEGER,
                outcome_duration_seconds REAL NOT NULL
                    CHECK(outcome_duration_seconds >= 0),
                outcome_timed_out INTEGER NOT NULL
                    CHECK(outcome_timed_out IN (0, 1)),
                gate_passed INTEGER CHECK(gate_passed IN (0, 1)),
                verification TEXT NOT NULL,
                gate_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS eval_runs_task_id_id
            ON eval_runs(task_id, id DESC)
            """
        )
        conn.commit()
        return conn
    except (OSError, sqlite3.Error) as exc:
        if "conn" in locals():
            conn.close()
        raise HistoryError(f"cannot open eval history: {exc}") from exc


def _history_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("eval_runs",),
        ).fetchone()
        is not None
    )


def _task_filter(task_id: str | None) -> tuple[str, tuple[str, ...]]:
    if task_id is None:
        return "", ()
    return "WHERE task_id = ?", (task_id,)


def _summary_from_total(row: sqlite3.Row) -> RunSummary:
    runs = int(row["runs"])
    passed = int(row["passed"])
    return RunSummary(
        runs=runs,
        passed=passed,
        failed=runs - passed,
        pass_rate=passed / runs if runs else None,
        average_agent_duration_seconds=row["average_agent_duration_seconds"],
        average_outcome_duration_seconds=row["average_outcome_duration_seconds"],
    )


def _summary_from_rows(rows: list[sqlite3.Row]) -> RunSummary:
    runs = len(rows)
    passed = sum(int(row["passed"]) for row in rows)
    return RunSummary(
        runs=runs,
        passed=passed,
        failed=runs - passed,
        pass_rate=passed / runs if runs else None,
        average_agent_duration_seconds=(
            sum(float(row["agent_duration_seconds"]) for row in rows) / runs
            if runs
            else None
        ),
        average_outcome_duration_seconds=(
            sum(float(row["outcome_duration_seconds"]) for row in rows) / runs
            if runs
            else None
        ),
    )


def _comparison(rows: list[sqlite3.Row], requested_window: int) -> TrendComparison:
    window_size = min(requested_window, len(rows) // 2)
    if window_size == 0:
        return TrendComparison(0, None, None, None, None, None)
    recent = _summary_from_rows(rows[:window_size])
    previous = _summary_from_rows(rows[window_size:window_size * 2])
    return TrendComparison(
        window_size=window_size,
        recent=recent,
        previous=previous,
        pass_rate_delta_percentage_points=(
            (recent.pass_rate - previous.pass_rate) * 100
            if recent.pass_rate is not None and previous.pass_rate is not None
            else None
        ),
        average_agent_duration_delta_seconds=_difference(
            recent.average_agent_duration_seconds,
            previous.average_agent_duration_seconds,
        ),
        average_outcome_duration_delta_seconds=_difference(
            recent.average_outcome_duration_seconds,
            previous.average_outcome_duration_seconds,
        ),
    )


def _difference(recent: float | None, previous: float | None) -> float | None:
    if recent is None or previous is None:
        return None
    return recent - previous


def _record_from_row(row: sqlite3.Row) -> RunRecord:
    gate_json = row["gate_json"]
    try:
        gate = json.loads(gate_json) if gate_json is not None else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise HistoryError(f"eval run {row['id']} contains invalid gate data") from exc
    if gate_json is not None and not isinstance(gate, dict):
        raise HistoryError(f"eval run {row['id']} contains invalid gate data")
    return RunRecord(
        id=int(row["id"]),
        recorded_at=row["recorded_at"],
        task_id=row["task_id"],
        passed=bool(row["passed"]),
        agent_exit_code=row["agent_exit_code"],
        agent_duration_seconds=float(row["agent_duration_seconds"]),
        agent_timed_out=bool(row["agent_timed_out"]),
        outcome_exit_code=row["outcome_exit_code"],
        outcome_duration_seconds=float(row["outcome_duration_seconds"]),
        outcome_timed_out=bool(row["outcome_timed_out"]),
        gate_passed=bool(row["gate_passed"]) if row["gate_passed"] is not None else None,
        verification=row["verification"],
        gate=gate,
    )


def _validate_report_bound(name: str, value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_REPORT_ROWS
    ):
        raise ValueError(f"{name} must be an integer from 1 to {MAX_REPORT_ROWS}")


def _empty_report(task_id: str | None) -> TrendReport:
    empty = RunSummary(0, 0, 0, None, None, None)
    return TrendReport(
        task_id=task_id,
        totals=empty,
        trend=TrendComparison(0, None, None, None, None, None),
        runs=(),
    )
