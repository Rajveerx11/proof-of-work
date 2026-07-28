"""Trusted-local benchmark execution for coding agents."""

from .harness import EvalResult, run_task
from .task import EvalTask, TaskValidationError, load_task

__all__ = ["EvalResult", "EvalTask", "TaskValidationError", "load_task", "run_task"]
