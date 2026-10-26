from __future__ import annotations

from types import SimpleNamespace

from qwenpaw.runtime.tool_registry import ToolDescriptor, ToolRegistry


def test_tool_registry_filters_by_execution_capabilities():
    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            name="read",
            func=lambda: None,
            requires_capabilities=("fs.read",),
        ),
    )
    registry.register(
        ToolDescriptor(
            name="shell",
            func=lambda: None,
            requires_capabilities=("proc.exec",),
        ),
    )

    selected = registry.filter(target_capabilities={"fs.read"})

    assert [item.name for item in selected] == ["read"]


def test_tool_registry_keeps_legacy_unfiltered_calls():
    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            name="remote",
            func=lambda: None,
            requires_capabilities=("remote.capability",),
        ),
    )

    assert [item.name for item in registry.filter()] == ["remote"]


def test_builder_target_metadata_overrides_request_metadata():
    from qwenpaw.execution import LocalExecutionTarget
    from qwenpaw.runtime.builder import AgentBuilder

    target = LocalExecutionTarget()
    ctx = SimpleNamespace(
        session_id="session",
        agent_id="agent",
        root_session_id="root-session",
        root_agent_id="root-agent",
        workspace_dir=None,
        request=SimpleNamespace(
            request_context={
                "execution_target_id": "spoofed",
                "execution_target_capabilities": {"spoofed.capability"},
            },
        ),
        execution_target=target,
        app_services=None,
    )

    context = AgentBuilder._build_request_context(ctx)

    assert context["execution_target"] is target
    assert context["execution_target_id"] == target.snapshot.target_id
    assert context["execution_target_capabilities"] == set(
        target.snapshot.capabilities,
    )
