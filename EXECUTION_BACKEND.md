# ExecutionBackend 需求与设计

> 状态：QwenPaw 通用执行目标接口已落地；远端实现与 AgentLoop 集成待实现
> 范围：qwenpaw 执行目标抽象、内置工具装配机制、插件扩展点
> 关联模块：`src/qwenpaw/agents/tools/`、`src/qwenpaw/runtime/`、`src/qwenpaw/plugins/`、`src/qwenpaw/app/workspace/`、`src/qwenpaw/config/`

---

## 1. 文档目的

本文定义 qwenpaw 的 **ExecutionTarget**（历史章节仍使用 ExecutionBackend 作为设计名）：一个由 qwenpaw 自有的、可插拔的抽象层，用于描述"agent 在哪个环境里读写文件、执行命令、观测屏幕"。

它同时定义与该抽象配套的 **工具装配机制**：内置工具不再是一份固定清单，而是按 ExecutionBackend 声明的能力动态装配；执行环境也可以自带工具族实现并直接接入。

本文只描述确定采纳的方案，包含背景、接口、能力模型、装配规则、配置模型、迁移清单与实施阶段。

其中 3.1、6.1、6.2 和第 7 节标注的是当前已落地的 QwenPaw 边界；
5.2、5.7 以及 P2-P5 是后续扩展设计，不能视为已经提供的 API。

当前 QwenPaw 已提供：

- `qwenpaw.execution.ExecutionTarget`、`ExecutionTargetSnapshot` 与 `LocalExecutionTarget`；
- `ContextVar` 形式的请求级 target 绑定；
- `ToolDescriptor.requires_capabilities` 与 `ToolSpec.requires_capabilities`；
- 插件注册 `register_execution_target_provider()` 的生命周期入口；
- `Read` / `Write` / `Edit` / `Append` / `Grep` / `Glob` / `Bash` 对非本地 target 的路由分支。

AgentLoop 的具体协议、网关和设备调度不进入 QwenPaw 核心，见 [`docs/agentloop-execution-target-integration.md`](docs/agentloop-execution-target-integration.md)。

---

## 2. 背景

### 2.1 当前架构事实

| 事实 | 证据 |
|---|---|
| 工具层直接操作宿主文件系统与宿主子进程 | `agents/tools/file_io.py`、`file_search.py`、`shell.py` 全部使用 `pathlib.Path` / `os` / `subprocess` |
| 路径解析只有一个入口，被 6 个模块共用 | `file_io.py:_resolve_file_path`，被 `file_io` / `file_search` / `ast_tool` / `send_file` / `view_media` / `lsp_tool` 引用 |
| 工具注册已支持动态增删 | `runtime/tool_registry.py:99-128` `register` / `register_many` / `unregister(name, expected)` |
| 工具过滤已支持声明式门禁 | `runtime/tool_registry.py:153-196` `filter(active_modes, active_skills, enabled_features, allowed, denied)` |
| agent 每次请求重建 toolkit | `runtime/builder.py:101` "Compose an agent for each request"；`build_toolkit()` 调用 `local_workspace.list_tools()` |
| 内置工具与插件工具共用同一 registry | `app/workspace/workspace.py:177` 将 `self.plugins.tool_registry` 注入 `QwenPawLocalWorkspace` |
| 插件已可注册工具且可撤销 | `plugins/sdk.py:543` `register_tool(ToolSpec) -> Registration` |
| 已有"多实现 + 可用性探测 + 显式不可用"的抽象先例 | `hub/provisioner.py` `RuntimeProvisioner(ABC)` / `RuntimeProvisionerAvailability` / `RuntimeProvisionerUnavailableError` |
| 执行链路完全自有，`agents/tools/` 与 `app/workspace/` 之间不存在任何执行环境抽象层 | 全仓不存在 backend 接口、`get_backend()` 或 Local 之外的实现 |

### 2.2 问题

**问题一：执行环境不可切换。**

`Read` / `Write` / `Edit` / `Append` / `Grep` / `Glob` / `Bash` 七个工具是 agent 的生存基础，它们全部假设"文件在宿主、命令在宿主跑"。没有任何一层描述"这次会话要在哪台机器上干活"。

**问题二：切换会静默出错，而不是报错。**

这是当前架构最危险的性质。假设只替换执行层而不动工具：

- `Bash` 在远端生成了 `dist/`，`Grep` 仍在宿主 `os.walk`，返回 "No matches found"。agent 会得出"文件不存在"的结论，然后重新生成。
- `Edit` 走远端、`Append` 走宿主，同一路径在两边各有一份，内容不同。
- `SendFileToUser` 在宿主 `os.path.exists`，远端产物一律"不存在"，用户拿不到结果。
- `shell.py` 的 `candidate.resolve()` 在宿主解析远端路径；若宿主恰好存在同名路径，命令会在**错误目录**执行。

全程零异常。

**问题三：工具能力与执行环境脱节。**

`Browser`、`DesktopScreenshot`、`LSP`、`AstSearch` 是否可用，取决于目标环境有没有 Chromium、有没有显示器、有没有 language server、有没有 `ast-grep`。当前没有任何机制表达这件事，只能靠运行时报错暴露。

同时，执行环境本身可能**已经具备**这些能力（云端开发机装了浏览器、云桌面有 VNC、远端装了 language server），却无法接入。

### 2.3 目标

1. 引入 `ExecutionBackend` 抽象，覆盖进程执行、文件读写、路径语义、屏幕观测、传输五类原语。
2. 内置工具按 backend 声明的能力**动态装配**：不可用的工具不出现在 tool list 中。
3. 提供 `ToolProvider` 机制，允许 backend 自带工具族实现并接入。
4. 全部能力以插件方式扩展，不修改核心代码即可新增 backend 或工具族。
5. 本地模式下行为与当前完全一致（零性能回退、零行为变化）。

### 2.4 非目标

- 不做远端与本地之间的工作区镜像 / 双向同步；这是具体 backend 实现的内部策略。
- 不改变治理层（`governance/`）的策略语义、审批流程与审计格式。
- 不引入新的配置 DSL；沿用既有 `BaseModel` 配置体系。
- 不在本阶段实现具体远端后端（SSH / 云沙箱），只定义接口、本地实现与插件扩展点。

---

## 3. 术语

| 术语 | 含义 |
|---|---|
| **ExecutionBackend** | 执行环境抽象。一个会话绑定一个实例，负责进程、文件、路径、屏幕、传输 |
| **capability** | 能力标识字符串，如 `fs.read`、`proc.pty`、`screen.capture` |
| **scope_id** | backend 实例的稳定标识，如 `local` / `ssh:dev-box:/home/dev/proj`。用于 session 分区与审计 |
| **ToolProvider** | 工具族实现，由 backend 或内置提供，按 `family` 解析 |
| **family** | 工具族名，如 `browser` / `computer_use` / `lsp` / `ast` |
| **装配（assemble）** | 一次 agent 请求开始时，按能力集与工具族选出本次可用的工具描述符 |

> 命名约定：`ExecutionBackend` 专指执行环境。既有配置字段 `config.backend` 语义不变（用于委托第三方 agent CLI 接管对话），两者不共用。

### 3.1 当前 QwenPaw API 映射

实现代码使用 `ExecutionTarget` 作为公共类型名，避免和已有 `config.backend` 配置字段混淆：

| 设计概念 | 当前 API |
|---|---|
| backend 实例 | `ExecutionTarget` |
| backend 元数据 | `ExecutionTargetSnapshot` |
| backend 请求选择 | `ExecutionTargetRequest` |
| backend 类型注册 | `PluginContext.register_execution_target_provider()` |
| 当前请求 backend | `get_current_execution_target()` |
| 本地实现 | `LocalExecutionTarget` |

AgentLoop plugin 应实现 `ExecutionTarget`，而不是把 AgentLoop 的控制面模型放入 QwenPaw。

---

## 4. 需求

### 4.1 功能需求

| 编号 | 需求 |
|---|---|
| FR-1 | 系统必须提供 `ExecutionBackend` 抽象，包含进程执行、文件读写、路径解析、屏幕观测、文件传输五类原语 |
| FR-2 | 系统必须提供本地 backend 实现，行为与当前完全一致 |
| FR-3 | 系统必须支持一个会话绑定一个 backend 实例，且会话 key 包含 `scope_id` |
| FR-4 | 系统必须在每次 agent 请求装配工具时，按 backend 的 `capabilities` 过滤工具 |
| FR-5 | 系统必须支持 backend 声明 `ToolProvider`，并按其 `family` 参与工具族解析 |
| FR-6 | 系统必须支持通过插件注册新的 backend 实现 |
| FR-7 | 系统必须在 backend 不可用或能力缺失时显式失败，不静默降级 |
| FR-8 | 系统必须把所有宿主路径解析收敛到 backend 的路径原语，工具层不得直接使用 `Path.resolve` / `Path.exists` 判定远端路径 |
| FR-9 | 系统必须在工具描述符与插件工具规格中支持 `requires_capabilities` |
| FR-10 | 系统必须在 backend 关闭时回收其登记的进程与连接 |
| FR-11 | 系统必须在审计记录中携带 `scope_id` |

### 4.2 非功能需求

| 编号 | 需求 |
|---|---|
| NFR-1 | 本地 backend 的文件与命令路径**不得**引入额外的进程跳转或数据拷贝 |
| NFR-2 | 能力过滤必须在装配期完成；调用期不得进行"能力探测 + 降级" |
| NFR-3 | 单个工具调用的执行时间不得因抽象层增加超过 5%（本地模式） |
| NFR-4 | backend 的凭据只存在于 qwenpaw 侧，不得下发给工具或模型 |
| NFR-5 | 远端 backend **不得**自我授权：其声明的工具族默认不覆盖内置实现 |
| NFR-6 | 新增 backend 不需要修改 `runtime/`、`governance/`、`config/` 之外的任何核心文件 |

### 4.3 验收标准

1. 绑定本地 backend 时，28 个工具的可用集合与当前完全一致，且全部现有测试通过。
2. 绑定一个只声明 `fs.read` 的测试 backend 时，tool list 中只剩 `Read`；`Grep`、`Bash`、`Edit` 等不出现。
3. 绑定一个声明 `fs.*` + `proc.exec` 的测试 backend 时，`Read` / `Write` / `Edit` / `Append` / `Grep` / `Glob` / `Bash` 全部可用，且路径解析全部经由 backend。
4. 换绑 backend 后，`effective_session_key` 变化，历史会话不被串用。
5. 卸载插件后，其注册的 backend 与工具族全部撤销，进行中的进程被 kill。
6. 审计日志中，每次工具调用可追溯 `scope_id`。

---

## 5. 设计方案

### 5.1 分层

```
┌──────────────────────────────────────────────────────────┐
│ Agent Loop（不感知执行环境）                                │
└──────────────────────────────────────────────────────────┘
                          │ tool list
┌──────────────────────────────────────────────────────────┐
│ 工具装配（registry.assemble）                              │
│   builtin descriptors × capabilities × tool families      │
└──────────────────────────────────────────────────────────┘
        │                              │
┌───────────────────┐      ┌──────────────────────────────┐
│ 内置工具           │      │ ToolProvider（工具族）         │
│ Read/Write/Edit    │      │ browser / computer_use /      │
│ Append/Grep/Glob   │      │ lsp / ast                     │
│ Bash               │      └──────────────────────────────┘
└───────────────────┘                      │
        └──────────────┬───────────────────┘
┌──────────────────────────────────────────────────────────┐
│ ExecutionBackend（进程 / 文件 / 路径 / 屏幕 / 传输）        │
└──────────────────────────────────────────────────────────┘
        │
┌───────────────────┬───────────────────┬──────────────────┐
│ LocalBackend      │ RemoteBackend …   │ SandboxBackend …  │
└───────────────────┴───────────────────┴──────────────────┘
```

三个不变量：

1. **agent loop 不感知 backend**——它只看到工具列表。
2. **工具不感知 backend 类型**——它只调用接口原语。
3. **backend 不感知工具**——它只声明能力与可选的工具族。

### 5.2 ExecutionBackend 接口

```python
# src/qwenpaw/execution/base.py

class ExecutionBackend(ABC):
    """一个会话绑定的执行环境。"""

    name: str                     # 实现名，如 "local" / "ssh"
    scope_id: str                 # 实例标识，如 "ssh:dev-box:/home/dev/proj"
    os_name: str                  # "posix" | "windows"
    capabilities: frozenset[str]  # 能力集，见 5.3

    # ---------------------------------------------------------- 生命周期
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def health(self) -> BackendHealth: ...

    # ------------------------------------------------------------ 进程
    async def exec(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        timeout: float | None = None,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> ExecResult: ...

    async def spawn(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        pty: bool = False,
    ) -> ProcessHandle: ...

    async def write_stdin(self, handle: ProcessHandle, data: bytes) -> None: ...
    async def read_output(
        self, handle: ProcessHandle, *, max_bytes: int
    ) -> OutputChunk: ...
    async def wait(
        self, handle: ProcessHandle, *, timeout: float | None = None
    ) -> ExecResult: ...
    async def kill(self, handle: ProcessHandle, *, tree: bool = True) -> None: ...

    # ------------------------------------------------------------ 文件
    async def read(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> bytes: ...
    async def write(
        self,
        path: str,
        data: bytes,
        *,
        mode: int | None = None,
        atomic: bool = True,
    ) -> None: ...
    async def append(self, path: str, data: bytes) -> None: ...
    async def stat(self, path: str) -> FileStat: ...
    async def list(
        self, path: str, *, recursive: bool = False
    ) -> list[DirEntry]: ...
    async def remove(self, path: str, *, recursive: bool = False) -> None: ...
    async def move(self, src: str, dst: str) -> None: ...

    # ------------------------------------------------------------ 路径
    def resolve(self, path: str, *, base: str | None = None) -> str:
        """把用户提供的路径解析为 backend 侧绝对路径。纯计算，不做 IO。"""

    def display(self, path: str) -> str:
        """把 backend 路径转为展示用相对路径。"""

    def lock_key(self, path: str) -> str:
        """进程内路径锁的键。远端不得使用宿主 Path.resolve。"""

    async def to_download_url(self, path: str) -> str:
        """把 backend 侧文件转为前端可访问的 URL（本地为 file://，远端为代理地址）。"""

    # ------------------------------------------------------------ 工具族
    def tool_providers(self) -> Mapping[str, ToolProvider]:
        """本 backend 自带的工具族实现，默认空。"""
        return {}
```

配套数据类型：

```python
@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int
    timed_out: bool = False

@dataclass(frozen=True)
class FileStat:
    path: str
    size: int
    mtime: float
    mode: int
    is_dir: bool
    is_symlink: bool

@dataclass(frozen=True)
class DirEntry:
    name: str
    path: str
    is_dir: bool
    is_symlink: bool

@dataclass(frozen=True)
class OutputChunk:
    data: bytes
    stream: str          # "stdout" | "stderr"
    eof: bool

@dataclass(frozen=True)
class BackendHealth:
    ok: bool
    detail: str = ""
```

设计要点：

- `exec` 与 `spawn` 分离。`exec` 是一次性收集，`spawn` 返回可交互句柄，`pty=True` 时提供持久终端语义（`cd` / `export` 保活）。
- `stdin` 是 `exec` 的一等参数，不允许靠写临时文件绕过。
- `kill(tree=True)` 是接口契约，不要求每个 backend 用同一机制实现（进程组 / Job Object / SSH channel 关闭）。
- `resolve` 是纯函数，不做 IO；这样能在装配期与鉴权期安全调用。
- `lock_key` 独立于 `resolve`。进程内锁需要的是规范化字符串，不是真实路径。

### 5.3 能力模型

能力是字符串常量，集中定义在一处，避免各 backend 自由发挥。

```python
# src/qwenpaw/execution/capabilities.py

FS_READ = "fs.read"
FS_WRITE = "fs.write"
FS_APPEND = "fs.append"
FS_STAT = "fs.stat"
FS_LIST = "fs.list"
FS_SEARCH_REGEX = "fs.search.regex"
FS_SEARCH_GLOB = "fs.search.glob"

PROC_EXEC = "proc.exec"
PROC_SPAWN = "proc.spawn"
PROC_PTY = "proc.pty"
PROC_KILL_TREE = "proc.kill_tree"

SCREEN_CAPTURE = "screen.capture"
SCREEN_INPUT = "screen.input"

BROWSER_SESSION = "browser.session"
LSP_SESSION = "lsp.session"
AST_PARSE = "ast.parse"

TRANSFER_UPLOAD = "transfer.upload"
TRANSFER_DOWNLOAD = "transfer.download"

NET_EGRESS = "net.egress"
```

规则：

- `capabilities` 是**声明**，不是探测结果。backend 在 `start()` 后必须保证声明的能力真实可用。
- `start()` 失败或 `health()` 为 false 时，调用方必须显式失败（`BackendUnavailableError`），不得继续用降级能力集。
- 本地 backend 的完整能力集即上表全集。

### 5.4 路径语义

路径语义是本次改造的核心风险点，必须显式收敛。

**收敇规则：**

> 任何"这个路径在目标环境里存不存在 / 是文件还是目录 / 绝对路径是什么"的判断，必须调用 `ExecutionBackend`，禁止直接使用 `Path.resolve` / `Path.exists` / `Path.is_dir` / `os.path.exists`。

**当前必须改造的六处宿主路径入口：**

| 位置 | 现状 | 改造 |
|---|---|---|
| `agents/tools/file_io.py:_resolve_file_path` | `Path.expanduser()` + `is_absolute()` | 改为 `backend.resolve()`，保留函数签名 |
| `agents/tools/shell.py:execute_shell_command` | `candidate.resolve()` | 改为 `backend.resolve()` |
| `agents/tools/file_search.py:_resolve_search_root` | `search_root.exists()` / `is_dir()` | 改为 `backend.stat()` |
| `agents/tools/ast_tool.py` | `subprocess.Popen(cwd=宿主路径)` | 改为 `backend.exec()` |
| `agents/tools/send_file.py` | `os.path.exists` / `os.path.isfile` | 改为 `backend.stat()` |
| `agents/tools/view_media.py` | `Path.exists()` / `is_file()` | 改为 `backend.stat()` |
| `agents/tools/lsp_tool.py` | `_resolve_file_path` + 宿主 LSP 进程 | 改为 `backend.resolve()` + `LSPProvider` |
| `utils/io_utils.py:_path_lock_key` | `Path(path).resolve(strict=False)` | 改为 `backend.lock_key()` |
| `agents/tools/desktop_screenshot.py` | 宿主屏幕尺寸 + `_path_to_file_url` | 改为 `backend.to_download_url()` + `ComputerUseProvider` |

**路径解析上下文：**

`resolve(path, base=...)` 的 `base` 来自会话上下文（当前工作目录）。装配期与工具调用期必须使用同一个 `base`，否则同一路径会在两处解析成不同结果。

### 5.5 工具装配：requires_capabilities

`ToolDescriptor` 增加一个字段：

```python
@dataclass(frozen=True)
class ToolDescriptor:
    ...
    requires_capabilities: tuple[str, ...] = ()
```

`tool_descriptor()` 增加同名关键字参数，透传到描述符。

`ToolRegistry.filter()` 增加一个入参：

```python
def filter(
    self,
    *,
    active_modes=None,
    active_skills=None,
    enabled_features=None,
    allowed=None,
    denied=None,
    capabilities: frozenset[str] | None = None,   # 新增
) -> list[ToolDescriptor]:
    ...
    caps = frozenset(capabilities or ())
    ...
    if d.requires_capabilities and not set(d.requires_capabilities).issubset(caps):
        continue
```

`capabilities=None` 表示不过滤，保持既有调用方（测试、CLI、无 backend 场景）行为不变。

**工具能力映射表：**

| 工具 | requires_capabilities |
|---|---|
| `Read` | `fs.read`, `fs.stat` |
| `Write` | `fs.write`, `fs.stat` |
| `Edit` | `fs.read`, `fs.write`, `fs.stat` |
| `Append` | `fs.write`, `fs.append`, `fs.stat` |
| `Grep` | `fs.read`, `fs.list`, `fs.search.regex` |
| `Glob` | `fs.list`, `fs.search.glob` |
| `Bash` | `proc.exec`, `fs.list` |
| `AstSearch` | `fs.read`, `fs.list`, `ast.parse` |
| `SendFileToUser` | `fs.read`, `fs.stat`, `transfer.download` |
| `ViewImage` | `fs.read`, `fs.stat`, `transfer.download` |
| `ViewVideo` | `fs.read`, `fs.stat`, `transfer.download` |
| `ListenAudio` | `fs.read`, `fs.stat`, `transfer.download` |
| `Browser` | `browser.session` |
| `DesktopScreenshot` | `screen.capture` |
| `LSP`（动态注册） | `lsp.session` |

**本地专属工具**：`DelegateExternalAgent` 依赖宿主 CLI 进程与宿主 `cwd`，标记 `requires_capabilities=("proc.exec", "host.cli")`；只有本地 backend 声明 `host.cli`。

**无依赖工具**（`WebSearch` / `WebFetch` / `RunToolBatch` / agent 管理与子代理工具 / `GetTokenUsage` / `GetCurrentTime` / `SetUserTimezone`）不声明能力，始终可用。

### 5.6 ToolProvider：工具族

```python
# src/qwenpaw/execution/families.py

class ToolProvider(Protocol):
    family: str                                  # "browser" | "computer_use" | "lsp" | "ast"
    priority: int                                # 数值大者优先

    def supports(self, capabilities: frozenset[str]) -> bool: ...
    def descriptors(self) -> list[ToolDescriptor]: ...
```

**工具族与 backend 的分工：**

| 归属 | 内容 | 理由 |
|---|---|---|
| `ExecutionBackend` 原语 | 进程、文件、路径、屏幕、传输 | 这些是所有工具共用的底层动作，不是"某个工具的实现" |
| `ToolProvider` 工具族 | `browser` / `computer_use` / `lsp` / `ast` | 这些有独立协议与观测模型，抽象层需要保留语义（如快照、可访问性树） |
| 内置工具（无 provider） | `Read` / `Write` / `Edit` / `Append` / `Grep` / `Glob` / `Bash` / 媒体与文件发送 | 它们是 backend 原语的 facade，不需要第二层抽象 |

**工具族协议：**

```python
class BrowserProvider(ToolProvider, Protocol):
    async def open(self, *, profile: str | None) -> BrowserSession: ...
    async def close(self, session: BrowserSession) -> None: ...
    async def snapshot(self, session: BrowserSession, *, max_nodes: int) -> PageSnapshot: ...
    async def act(self, session: BrowserSession, action: BrowserAction) -> ActionResult: ...
    async def screenshot(
        self, session: BrowserSession, *, full_page: bool = False
    ) -> bytes: ...

class ComputerUseProvider(ToolProvider, Protocol):
    async def screen_size(self) -> tuple[int, int]: ...
    async def screenshot(self, *, region: Rect | None = None) -> bytes: ...
    async def act(self, action: ComputerAction) -> None: ...
```

约束：

- `PageSnapshot`（可访问性树 + 元素标识 + 可交互标记）是 **协议的一部分**，不是实现细节。任何 browser provider 都必须产出统一快照，否则上层推理链无法复用。
- `ComputerUseProvider.screen_size()` 返回**目标环境**的分辨率；截图与坐标系必须同源。
- 工具族描述符同样携带 `requires_capabilities`，装配期统一过滤。

### 5.7 装配流程与优先级

```python
def assemble_tools(
    registry: ToolRegistry,
    backend: ExecutionBackend,
    *,
    active_modes: set[str],
    active_skills: set[str],
    enabled_features: set[str],
    allowed: set[str] | None,
    denied: set[str],
    allow_backend_override: bool,
) -> list[ToolDescriptor]:
    caps = backend.capabilities

    # 1. 内置工具
    descs = registry.filter(
        active_modes=active_modes,
        active_skills=active_skills,
        enabled_features=enabled_features,
        allowed=allowed,
        denied=denied,
        capabilities=caps,
    )

    # 2. 工具族解析：内置 provider 优先，backend provider 填补空缺
    families: dict[str, ToolProvider] = dict(BUILTIN_PROVIDERS)
    for family, provider in backend.tool_providers().items():
        if not provider.supports(caps):
            continue
        if allow_backend_override or family not in families:
            families[family] = provider

    # 3. 用选中的 provider 替换该族的工具描述符
    overridden = set(families)
    descs = [d for d in descs if d.metadata.get("family") not in overridden]
    for provider in families.values():
        descs.extend(
            d for d in provider.descriptors()
            if set(d.requires_capabilities).issubset(caps)
        )

    return descs
```

**优先级规则：**

1. `denied` 最高。
2. `allowed` 次之。
3. 内置工具族默认优先；backend 提供的族只有在**未冲突**或 `allow_backend_override=true` 时生效。
4. `allow_backend_override` 默认 `false`，按 agent 配置开启。

**为什么默认不覆盖：** 远端环境声称"我有 browser"，不应自动获得宿主 browser 的登录态与凭据。覆盖必须由 qwenpaw 侧显式授权。

### 5.8 会话分区与生命周期

**会话键：**

```python
effective_session_key = f"{base_session_id}@{backend.scope_id}"
```

`base_session_id` 沿用 `channel:user_id` 规则；`scope_id` 标识执行环境实例。换绑 backend 即换会话，历史消息不被串用。

**生命周期：**

| 事件 | 动作 |
|---|---|
| 会话首次绑定 backend | `await backend.start()`；失败则显式拒绝该会话 |
| 每次 agent 请求 | 复用已启动的 backend，`assemble_tools()` |
| 会话结束 | `await backend.close()` |
| 插件卸载 | 注销其 backend → `close()` → 回收进程与连接 |
| 进程登记 | `spawn` / `exec(on_output=...)` 的句柄登记在 backend 内部表；`close()` 时统一 `kill(tree=True)` |

**并发：** 一个 backend 实例可服务同一会话内的并发调用；跨会话不共享实例，除非配置显式声明共享池。

### 5.9 配置模型

顶层新增 `ExecutionConfig`：

```python
class ExecutionBackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str                                  # "local" | "ssh" | 插件注册的类型
    settings: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] | None = None      # 覆盖实现声明的能力集（仅收缩，不扩张）

class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str = "local"
    backends: dict[str, ExecutionBackendConfig] = Field(default_factory=dict)
    allow_backend_override: bool = False
```

agent 级指定：

```python
class AgentConfig(BaseModel):
    ...
    execution_backend: str | None = None       # None = 使用 ExecutionConfig.default
```

示例：

```yaml
execution:
  default: local
  allow_backend_override: false
  backends:
    local:
      type: local
    devbox:
      type: ssh
      settings:
        host: dev-box.internal
        port: 22
        user: dev
        workdir: /home/dev/project
        identity_file: ~/.ssh/id_ed25519

agents:
  profiles:
    default:
      execution_backend: devbox
```

规则：

- `capabilities` 只能收缩（取交集），不能扩张。防止配置写错导致工具误判定为可用。
- `settings` 由各实现自行校验，核心不感知字段。

### 5.10 插件扩展点

插件通过 `PluginContext` 注册：

```python
class Plugin:
    def register(self, ctx: PluginContext) -> None:
        ctx.execution_backends.register("ssh", SSHRuntimeFactory)
        ctx.tool_providers.register("browser", CDPBrowserProvider)
```

契约：

- `register()` 返回 `Registration`，`release()` 时依次撤销 registry 条目 → 关闭该 backend 所有实例 → 回收进程。
- 插件卸载后，其 backend 不可再被新会话绑定；已绑定的会话收到 `BackendUnavailableError` 并由上层转为显式失败。
- 插件声明的 `type` 与内置 `type` 冲突时，`register` 抛出与现有工具注册一致的占用冲突错误（复用 `occupancy_conflict`）。

### 5.11 安全与治理

| 项 | 规则 |
|---|---|
| 凭据 | 只存在于 qwenpaw 侧的后端连接对象中，不下发给模型、工具、日志 |
| 自我授权 | backend 声明的工具族默认不覆盖内置；覆盖需 `allow_backend_override` |
| 能力收缩 | 配置只能收缩能力集，不能扩张 |
| 审计 | 每条工具调用记录 `scope_id`、`backend.name`、`argv` 摘要、退出码、耗时 |
| 治理正交 | `PolicyGuardedTool` / 策略判定 / 审批流程不改动，`scope_id` 作为审计字段附加 |
| 失败可见 | backend 不可用、能力缺失、超时、连接断开一律显式错误，不返回空结果 |

### 5.12 可观测性

- 结构化日志字段：`scope_id`、`backend.name`、`op`（exec/read/write/stat/...）、`duration_ms`、`bytes`、`exit_code`。
- 指标：每 backend 的调用数、错误率、P50/P95 延迟、活跃进程数。
- 错误分类：`BackendUnavailableError` / `CapabilityMissingError` / `BackendTimeoutError` / `BackendIOError`。

---

## 6. 迁移清单

### 6.1 新增文件

| 文件 | 内容 |
|---|---|
| `src/qwenpaw/execution/__init__.py` | 导出公共类型 |
| `src/qwenpaw/execution/base.py` | `ExecutionTarget` protocol、结果类型、请求上下文和 ContextVar |
| `src/qwenpaw/execution/local.py` | `LocalExecutionTarget`，本地文件与命令原语 |
| `src/qwenpaw/execution/registry.py` | 每个 workspace 的 target provider 注册表 |

### 6.2 修改文件

| 文件 | 改动 |
|---|---|
| `runtime/tool_registry.py` | `ToolDescriptor.requires_capabilities`；`tool_descriptor()` 新参数；`filter()` 新入参 |
| `app/workspace/local_workspace.py` | `list_tools()` 接受 target capabilities 并过滤工具 |
| `runtime/builder.py` | 从上下文取 backend，透传 capabilities |
| `agents/tools/file_io.py` | 4 个工具改走 backend 原语；`_resolve_file_path` 改为 `backend.resolve` |
| `agents/tools/file_search.py` | `Grep` / `Glob` 对非本地 target 走 target 搜索原语 |
| `agents/tools/shell.py` | 非本地 target 走 target 执行原语；本地保留现有 shell/sandbox 路径 |
| `agents/tools/ast_tool.py` / `browser.py` / `desktop_screenshot.py` | 声明 local-only capability，远端 target 默认不装配 |
| `agents/tools/send_file.py` / `view_media.py` | 声明 local-only capability，远端产物链路待 P2 |
| `agents/tools/delegate_external_agent.py` | 声明 `host.cli` 能力 |
| `plugins/sdk.py` | 新增 `register_execution_target_provider` |
| `plugins/tools.py` | `ToolSpec` 新增 `requires_capabilities` |

### 6.3 不改动

`governance/`（除审计字段附加）、`hub/`、`channels/`、`memory/`、`modes/`、`loop/`、`console/`。

---

## 7. 实施阶段

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| **P0** | `ExecutionTarget` 接口 + `LocalExecutionTarget` + `capabilities` + `requires_capabilities` + provider 装配链路 | 已落地；本地 target 默认绑定，工具集可按 target capability 收缩 |
| **P1** | 七个核心工具（`Read`/`Write`/`Edit`/`Append`/`Grep`/`Glob`/`Bash`）改走 target 原语 | 已落地路由分支；远端 target 由外部插件提供 |
| **P2** | 产物链路（`SendFileToUser`/`ViewImage`/`ViewVideo`/`ListenAudio`）+ `to_download_url` | 待后续；当前这些工具仍要求本地能力 |
| **P3** | `ToolProvider` 协议 + 工具族解析 + 插件注册点；`computer_use` 族（本地实现） | 待后续；当前仅提供 target provider 与 capability 门禁 |
| **P4** | `browser` 族重构：先定 `PageSnapshot` 协议，再抽象 CDP 实现 | 待后续 |
| **P5** | 首个远端 backend 实现（SSH） | 由外部插件实现；不进入 QwenPaw 核心 |

P0 与 P1 是关键验证点：如果 P0 的能力收缩机制不成立，后续阶段全部要重做。

---

## 8. 开放问题

| 编号 | 问题 | 影响 |
|---|---|---|
| Q1 | `Grep` / `Glob` 在远端没有 `rg` 时，是声明 `fs.search.*` 不可用（工具消失），还是由 backend 内部实现兜底（如上传 helper 脚本）？ | 决定 P1 的搜索层实现形态 |
| Q2 | 传输大文件时，`to_download_url` 是返回代理地址（流式）还是先落地本地缓存？ | 影响前端与小内存设备 |
| Q3 | 是否允许一个会话在运行中热切换 backend？若允许，进行中的工具调用如何收尾？ | 影响 5.8 生命周期与 UI |
| Q4 | 插件提供的 backend 是否需要声明信任级别（本地可信 / 远端不可信）以影响默认 `allow_backend_override`？ | 影响 5.7 与 5.11 |
| Q5 | `pty` 能力是否在 P1 就必须具备，还是留给远端 backend 实现阶段？ | 影响接口最小可用集 |
