"""Execution target contracts and request-scoped target helpers."""

from .base import (
    ExecutionResult,
    ExecutionTarget,
    ExecutionTargetRequest,
    ExecutionTargetSnapshot,
    TargetHealth,
    get_current_execution_target,
    reset_execution_target,
    set_execution_target,
)
from .local import LOCAL_CAPABILITIES, LocalExecutionTarget
from .registry import ExecutionTargetProvider, ExecutionTargetRegistry

__all__ = [
    "ExecutionResult",
    "ExecutionTarget",
    "ExecutionTargetProvider",
    "ExecutionTargetRegistry",
    "ExecutionTargetRequest",
    "ExecutionTargetSnapshot",
    "LOCAL_CAPABILITIES",
    "LocalExecutionTarget",
    "TargetHealth",
    "get_current_execution_target",
    "reset_execution_target",
    "set_execution_target",
]
