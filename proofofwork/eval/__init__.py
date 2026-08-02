"""Trusted-local benchmark execution for coding agents."""

from .harness import EvalResult, UsageMetrics, run_task
from .history import (
    DEFAULT_HISTORY_DB,
    HistoryError,
    RunRecord,
    TrendReport,
    build_report,
    record_run,
)
from .task import EvalTask, GatePolicy, TaskValidationError, load_task

__all__ = [
    "DEFAULT_HISTORY_DB",
    "EvalResult",
    "EvalTask",
    "GatePolicy",
    "HistoryError",
    "RunRecord",
    "TaskValidationError",
    "TrendReport",
    "UsageMetrics",
    "build_report",
    "load_task",
    "record_run",
    "run_task",
]
