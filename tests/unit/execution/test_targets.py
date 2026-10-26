from __future__ import annotations

import asyncio

from qwenpaw.execution import (
    ExecutionTargetProvider,
    ExecutionTargetRegistry,
    ExecutionTargetRequest,
    LocalExecutionTarget,
    get_current_execution_target,
    reset_execution_target,
    set_execution_target,
)


def test_local_target_reads_and_writes(tmp_path):
    target = LocalExecutionTarget()
    path = target.resolve("sample.txt", base=str(tmp_path))

    asyncio.run(target.write_text(path, "hello"))
    assert asyncio.run(target.read_text(path)) == "hello"
    assert target.snapshot.target_id == "local"
    assert "fs.read" in target.snapshot.capabilities


def test_target_context_binding_is_scoped():
    target = LocalExecutionTarget()
    token = set_execution_target(target)
    try:
        assert get_current_execution_target() is target
    finally:
        reset_execution_target(token)


def test_registry_resolves_provider_before_local_fallback():
    registry = ExecutionTargetRegistry()
    target = LocalExecutionTarget()

    async def provider(_request):
        return target

    registry.register(
        ExecutionTargetProvider(
            name="test",
            factory=provider,
            priority=10,
        ),
    )
    request = ExecutionTargetRequest(
        workspace_id="workspace",
        agent_id="agent",
        session_id="session",
        workspace_dir=None,
    )

    assert asyncio.run(registry.resolve(request)) is target


def test_registry_rejects_incomplete_target():
    registry = ExecutionTargetRegistry()

    class IncompleteTarget:
        snapshot = object()
        is_local = False
        resolve = None

    async def provider(_request):
        return IncompleteTarget()

    registry.register(
        ExecutionTargetProvider(
            name="incomplete",
            factory=provider,
        ),
    )

    request = ExecutionTargetRequest(
        workspace_id="workspace",
        agent_id="agent",
        session_id="session",
        workspace_dir=None,
    )
    try:
        asyncio.run(registry.resolve(request))
    except TypeError as exc:
        assert "missing required attributes" in str(exc)
    else:
        raise AssertionError("incomplete target should be rejected")
