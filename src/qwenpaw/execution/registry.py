"""Per-workspace execution-target provider registry."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from .base import (
    ExecutionTarget,
    ExecutionTargetRequest,
    Provider,
)
from .local import LocalExecutionTarget


@dataclass(frozen=True)
class ExecutionTargetProvider:
    name: str
    factory: Provider
    priority: int = 100
    owner_plugin_id: str = ""


class ExecutionTargetRegistry:
    """Resolve one request-scoped target from plugin-owned providers."""

    def __init__(self) -> None:
        self._providers: dict[str, ExecutionTargetProvider] = {}

    def register(self, provider: ExecutionTargetProvider) -> None:
        if not provider.name or not callable(provider.factory):
            raise TypeError("execution target providers require a name and factory")
        if provider.name in self._providers:
            existing = self._providers[provider.name]
            raise ValueError(
                "execution target provider "
                f"{provider.name!r} is already registered by "
                f"{existing.owner_plugin_id or 'another provider'}",
            )
        self._providers[provider.name] = provider

    def unregister(
        self,
        name: str,
        *,
        expected: ExecutionTargetProvider | None = None,
    ) -> bool:
        current = self._providers.get(name)
        if current is None or (expected is not None and current is not expected):
            return False
        del self._providers[name]
        return True

    def names(self) -> list[str]:
        return sorted(self._providers)

    async def resolve(self, request: ExecutionTargetRequest) -> ExecutionTarget:
        providers = sorted(
            self._providers.values(),
            key=lambda item: (item.priority, item.name),
        )
        for provider in providers:
            target = provider.factory(request)
            if inspect.isawaitable(target):
                target = await target
            if target is not None:
                self._validate_target(target)
                return target
        return LocalExecutionTarget()

    @staticmethod
    def _validate_target(target: Any) -> None:
        required = (
            "snapshot",
            "is_local",
            "resolve",
            "health",
            "read_text",
            "write_text",
            "append_text",
            "execute",
            "grep",
            "glob",
        )
        missing = [name for name in required if not hasattr(target, name)]
        if missing:
            raise TypeError(
                "execution target is missing required attributes: "
                + ", ".join(missing),
            )
        non_callable = [
            name
            for name in required
            if name not in {"snapshot", "is_local"}
            and not callable(getattr(target, name, None))
        ]
        if non_callable:
            raise TypeError(
                "execution target has non-callable members: "
                + ", ".join(non_callable),
            )


__all__ = ["ExecutionTargetProvider", "ExecutionTargetRegistry"]
