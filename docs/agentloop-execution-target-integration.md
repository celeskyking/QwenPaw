# AgentLoop Execution Target Integration

本文定义 AgentLoop 如何作为 QwenPaw 的外部执行目标提供方。本文只描述 AgentLoop 项目需要提供的协议和组件，不要求 QwenPaw 引入 AgentLoop 的 proto、设备 ID 或凭据。

## 1. 边界

```text
QwenPaw Harness
  └── agentloop-qwenpaw plugin
        ├── 绑定 AgentDefinition
        ├── 维护 revision / target snapshot
        └── 返回 RemoteExecutionTarget
                         │
                         ▼
                 AgentLoop Execution Gateway
                         │
                         ▼
                  AIO Sandbox 云设备
```

- QwenPaw 负责 agent loop、工具装配、治理和请求生命周期。
- AgentLoop 负责 AgentDefinition、revision、设备生命周期、权限、租约和审计。
- `harness-qwenpaw` 负责把 AgentLoop 控制面和数据面适配成 QwenPaw plugin。
- Harness 的部署位置与执行目标完全独立。Harness 不需要部署在目标设备上。

## 2. AgentLoop 侧数据模型

现有 `ManagedRuntimeBinding` 描述 Harness endpoint，不能表达 Harness 实际要操作的设备。建议在 AgentLoop 的 revision runtime profile 中增加独立的 `ExecutionTargetBinding`：

```text
ExecutionTargetBinding
  target_id
  device_id / sandbox_id
  workspace
  working_directory
  required_capabilities[]
  priority
  binding_generation
```

`DeviceAgentBinding` 继续表示“设备上安装了某个 Agent runtime”。如果必须复用该消息，应增加明确的 binding role；不要让同一个字段同时表示 Harness 安装位置和远程工具执行位置。

AgentLoop 必须为每次 Harness invocation 生成短期 `ExecutionGrant`：

```text
workspace
agent_revision
run_id
session_id
target_id
binding_generation
allowed_capabilities[]
working_directory
expires_at
audience = qwenpaw-harness
```

Grant 由 Gateway 校验。QwenPaw plugin 不接收用户 JWT、设备 daemon token 或裸 execd 凭据。

## 3. Execution Gateway

Gateway 是 Harness 和云设备之间的唯一执行入口，至少提供：

```text
POST /execution/v1/targets/{target}/exec
GET  /execution/v1/targets/{target}/files/read
PUT  /execution/v1/targets/{target}/files/write
POST /execution/v1/targets/{target}/files/append
POST /execution/v1/targets/{target}/files/edit
POST /execution/v1/targets/{target}/search/grep
POST /execution/v1/targets/{target}/search/glob
GET  /execution/v1/targets/{target}/health
```

实际 URL 可以不同，但必须满足这些语义：

- 每个请求携带并校验 `ExecutionGrant`；
- 校验 `workspace`、`agent_revision`、`run_id`、`session_id`、`target_id` 和 `binding_generation`；
- Gateway 解析当前 sandbox / execd endpoint，不把 Pod 地址暴露给 Harness；
- Gateway 负责超时、取消、输出大小限制和审计；
- Harness 主机的 shell、环境变量和凭据不透传到云设备；target 自己决定
  shell 语义和默认环境，调用方只传递明确授权的 `env`；
- QwenPaw 的本地治理层对远端文件使用 target 的逻辑路径，不把路径改写到
  Harness 本机项目目录；设备级权限和最终审计仍由 Gateway 负责。
- 目标设备变化或 grant 失效时返回明确的 `target_unavailable` / `grant_expired` 错误；
- 不允许 Gateway 静默切换到另一台设备，除非新的 target generation 已被 AgentLoop 控制面确认。

## 4. QwenPaw plugin contract

AgentLoop plugin 使用 QwenPaw 的通用入口：

```python
def register(ctx):
    ctx.register_execution_target_provider(
        "agentloop",
        provider.resolve,
        priority=20,
    )
```

Provider 收到 `ExecutionTargetRequest` 后，根据绑定的 agent、session 和请求上下文读取当前 reconciled snapshot，并返回 `RemoteExecutionTarget`。Remote target 只实现 QwenPaw 的 `ExecutionTarget` protocol：

- `snapshot`：target id、generation、capabilities；
- `resolve()`：远程工作目录的路径语义；
- `read_text()` / `write_text()` / `append_text()`；
- `execute()`；
- `grep()` / `glob()`；
- `health()`。

QwenPaw 核心不需要知道 AgentLoop 的 proto 类型。

## 5. 动态 reconcile

Plugin 启动时先读取完整 AgentDefinition 和当前 revision，然后启动两个独立任务：

1. 订阅 workspace 级 AgentSpec SSE；事件只作为 refresh 唤醒信号；
2. 按固定间隔轮询完整 definition、revision、target status，作为断线兜底。

所有控制面数据合并成不可变 snapshot，并计算 fingerprint：

```text
definition + revision + runtime profile + target binding + capabilities
```

更新规则：

- 只有 prompt/model/assets 变化：更新 Agent 配置，不替换 execution target；
- target binding 变化：进入 `draining`，停止新工具调用，等待旧调用完成，撤销旧 grant，创建新 target，做 health/capability check，再发布新 generation；
- 目标状态变化但 binding 不变：重新 health check 或重新申请 grant，不重建 QwenPaw agent；
- refresh 失败：保留最后一个健康 snapshot，但超过租约后停止远程工具调用；
- 任何 target 不可用都必须显式失败，禁止回退到 Harness 本地环境。

## 6. Session 与 generation

Session 必须固定到：

```text
session_id + target_id + binding_generation
```

设备切换后，旧 session 不能静默继续使用新设备。可选策略是：

- 旧 session 结束后由上层创建新 session；或
- AgentLoop 显式发起 migration，并把新 generation 作为一次可审计的 session 变更。

## 7. `harness-qwenpaw` 重构清单

当前 launcher 中的 provider 注册、`agent.json` 写入和 SSE reconcile 属于外部编排逻辑。后续建议收缩为：

```text
harness-qwenpaw/
  plugin/
    agentloop_plugin.py
    control_plane_client.py
    reconciler.py
    execution_target.py
  adapter/
    server.py
  bootstrap.py
```

- `bootstrap.py` 只启动 QwenPaw 和 Harness HTTP adapter；
- `agentloop_plugin.py` 注册 provider、工具和生命周期；
- `reconciler.py` 管理 SSE、轮询、generation 和 drain/swap；
- `execution_target.py` 只调用 Execution Gateway；
- mapping 函数保留为纯数据转换，不再作为第二套 QwenPaw runtime 管理器。

## 8. AgentLoop 实施顺序

1. 增加 `ExecutionTargetBinding`、generation 和 target capability snapshot；
2. 增加 `ExecutionGrant` 创建和校验；
3. 增加 Execution Gateway 的 exec/file/search/health API；
4. 增加 target 状态事件和 revision reconcile 触发；
5. 将 `harness-qwenpaw` 改为 QwenPaw plugin；
6. 用远程 AIO Sandbox 做端到端测试，覆盖设备替换、设备离线、grant 过期、取消和审计。
