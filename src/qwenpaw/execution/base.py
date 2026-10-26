"""Generic execution target contracts used by QwenPaw tools and plugins."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ExecutionResult:
    """Result of a command executed by an :class:`ExecutionTarget`."""

    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class TargetHealth:
    """Point-in-time health reported by an execution target."""

    status: str = "ready"
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class ExecutionTargetSnapshot:
    """Stable metadata used for tool assembly, sessions, and audit."""

    target_id: str
    generation: str
    capabilities: frozenset[str] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionTargetRequest:
    """Request identity passed to execution-target providers."""

    workspace_id: str
    agent_id: str
    session_id: str
    workspace_dir: str | None
    request_context: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExecutionTarget(Protocol):
    """Execution environment consumed by QwenPaw tools.

    Implementations may be local, SSH-backed, or backed by a cloud sandbox.
    The protocol deliberately contains no provider-specific authentication or
    device concepts.
    """

    @property
    def snapshot(self) -> ExecutionTargetSnapshot: ...

    @property
    def is_local(self) -> bool: ...

    def resolve(self, path: str, *, base: str | None = None) -> str: ...

    async def health(self) -> TargetHealth: ...

    async def read_text(self, path: str, *, encoding: str = "utf-8") -> str: ...

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = True,
    ) -> None: ...

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> None: ...

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult: ...

    async def grep(
        self,
        pattern: str,
        *,
        path: str | None = None,
        is_regex: bool = False,
        case_sensitive: bool = True,
        context_lines: int = 0,
        include_pattern: str | None = None,
        show_file: bool = True,
    ) -> str: ...

    async def glob(self, pattern: str, *, path: str | None = None) -> list[str]: ...


_current_execution_target: ContextVar[ExecutionTarget | None] = ContextVar(
    "qwenpaw_current_execution_target",
    default=None,
)


def set_execution_target(target: ExecutionTarget) -> object:
    """Bind *target* to the current request context."""

    return _current_execution_target.set(target)


def reset_execution_target(token: object) -> None:
    """Restore the execution target that preceded a request."""

    _current_execution_target.reset(token)  # type: ignore[arg-type]


def get_current_execution_target() -> ExecutionTarget:
    """Return the request target, defaulting to the local implementation."""

    target = _current_execution_target.get()
    if target is not None:
        return target
    from .local import LocalExecutionTarget

    return LocalExecutionTarget()


Provider = Callable[
    [ExecutionTargetRequest],
    ExecutionTarget | None | Awaitable[ExecutionTarget | None],
]


__all__ = [
    "ExecutionResult",
    "ExecutionTarget",
    "ExecutionTargetRequest",
    "ExecutionTargetSnapshot",
    "Provider",
    "TargetHealth",
    "get_current_execution_target",
    "reset_execution_target",
    "set_execution_target",
]
