# QwenPaw Execution Target Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic, plugin-owned execution target contract to QwenPaw while documenting the AgentLoop-specific gateway and binding work separately.

**Architecture:** QwenPaw owns only the execution target protocol, local implementation, request-scoped target binding, capability-aware tool selection, and plugin registration hooks. AgentLoop remains responsible for AgentDefinition, device lifecycle, grants, target resolution, and the remote gateway; its integration is documented as a future external plugin implementation.

**Tech Stack:** Python 3.11+, dataclasses, asyncio, ContextVar, existing QwenPaw plugin/runtime/tool registry, pytest.

## Global Constraints

- Preserve local-mode behavior and existing governance semantics.
- Do not add AgentLoop-specific URLs, proto types, or device credentials to QwenPaw.
- Do not change the existing `config.backend` meaning.
- Remote execution must fail closed and must not silently fall back to the local target.
- Keep existing plugin lifecycle cleanup semantics.

---

### Task 1: Define execution target contracts

**Files:**
- Create: `src/qwenpaw/execution/__init__.py`
- Create: `src/qwenpaw/execution/base.py`
- Create: `src/qwenpaw/execution/local.py`
- Test: `tests/unit/execution/test_targets.py`

**Interfaces:**
- Produces `ExecutionTarget`, `ExecutionTargetSnapshot`, `ExecutionResult`, `TargetHealth`, `LocalExecutionTarget`.
- Provides `get_current_execution_target`, `set_execution_target`, and `reset_execution_target` context helpers.

- [x] Add immutable target metadata and async process/file primitives.
- [x] Implement `LocalExecutionTarget` using the existing workspace root and subprocess behavior.
- [x] Add tests for local path resolution, file operations, command execution, capability reporting, and context binding.

### Task 2: Add target-aware request and toolkit assembly

**Files:**
- Modify: `src/qwenpaw/runtime/hooks.py`
- Modify: `src/qwenpaw/runtime/runtime.py`
- Modify: `src/qwenpaw/runtime/builder.py`
- Modify: `src/qwenpaw/app/workspace/local_workspace.py`
- Modify: `src/qwenpaw/runtime/tool_registry.py`
- Modify: `src/qwenpaw/plugins/tools.py`
- Modify: `src/qwenpaw/plugins/sdk.py`
- Test: `tests/unit/runtime/test_execution_target_context.py`

**Interfaces:**
- `HookContext.execution_target` carries the per-request target.
- `ToolDescriptor.requires_capabilities` and `ToolSpec.requires_capabilities` declare target requirements.
- `ToolRegistry.filter(..., target_capabilities=...)` filters unavailable tools.
- `PluginContext.register_execution_target_provider()` registers an owned target provider.

- [x] Extend request context construction with the workspace target snapshot.
- [x] Bind the selected target around agent build and execution.
- [x] Filter descriptors and plugin tools by target capabilities without changing governance checks.
- [x] Add lifecycle-safe provider registration and teardown.
- [x] Test local target availability and capability filtering.

### Task 3: Route core file and shell tools through the target

**Files:**
- Modify: `src/qwenpaw/agents/tools/file_io.py`
- Modify: `src/qwenpaw/agents/tools/file_search.py`
- Modify: `src/qwenpaw/agents/tools/shell.py`
- Modify: `src/qwenpaw/agents/tools/send_file.py`
- Modify: `src/qwenpaw/agents/tools/view_media.py`
- Test: `tests/unit/agents/tools/test_execution_target_tools.py`

**Interfaces:**
- Core tools call `get_current_execution_target()` for path and process/file primitives.
- Local target preserves current output and error behavior.

- [x] Move path resolution and basic file reads/writes through the target facade.
- [x] Move shell command execution through the target facade while preserving local timeout and cancellation behavior.
- [x] Keep local-only media/file URL behavior explicit in `LocalExecutionTarget`.
- [x] Add fake-target tests for file I/O, search, and shell routing.

### Task 4: Document AgentLoop integration contract

**Files:**
- Modify: `EXECUTION_BACKEND.md`
- Create: `docs/agentloop-execution-target-integration.md`

**Interfaces:**
- Documents the external `AgentLoopExecutionTargetProvider` contract, target snapshot/generation semantics, gateway/grant requirements, and dynamic reconciliation behavior.

- [x] Reframe the document around QwenPaw generic contracts plus external providers.
- [x] Specify AgentLoop responsibilities without adding AgentLoop code to QwenPaw.
- [x] Describe SSE/polling, target status changes, drain/swap behavior, session pinning, and fail-closed rules.
- [x] Add an implementation checklist for the future `harness-qwenpaw` plugin.

### Task 5: Validate local compatibility

**Files:**
- Test: existing focused runtime/tool tests and new execution target tests.

- [x] Run focused syntax and direct execution-target smoke checks; pytest remains blocked by the installed AgentScope mismatch.
- [ ] Run the existing tool and plugin integration tests affected by registry and lifecycle changes.
- [x] Run `compileall` and `git diff --check`; no standalone formatter or type checker is configured in this checkout.

Validation is currently blocked by the checked-out environment: Python 3.14 has
an incompatible AgentScope installation, while Python 3.12 has no pytest or
runtime dependencies installed.
