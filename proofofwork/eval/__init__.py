"""Trusted-local benchmark execution for coding agents."""

from .adapters import (
    ADAPTERS,
    AdapterValidationError,
    AgentInvocation,
    build_agent_invocation,
)
from .harness import EvalResult, UsageMetrics, run_task
from .history import (
    DEFAULT_HISTORY_DB,
    HistoryError,
    RunRecord,
    TrendReport,
    build_report,
    record_run,
)
from .report import render_html
from .task import EvalTask, GatePolicy, TaskValidationError, load_task

__all__ = [
    "ADAPTERS",
    "DEFAULT_HISTORY_DB",
    "AdapterValidationError",
    "AgentInvocation",
    "EvalResult",
    "EvalTask",
    "GatePolicy",
    "HistoryError",
    "RunRecord",
    "TaskValidationError",
    "TrendReport",
    "UsageMetrics",
    "build_agent_invocation",
    "build_report",
    "load_task",
    "record_run",
    "render_html",
    "run_task",
]
