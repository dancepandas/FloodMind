# FloodMind SDK 维护建议

> 审查范围：FloodMind SDK v1.1.9 源码、开发指南及针对性单元测试。  
> 审查方式：只读源码审查与聚焦测试，不包含业务项目 E2E。  
> 目标：记录 Skill 体系以外的已确认缺陷、维护风险、修复方向和推荐实施顺序。

## 1. 总体结论

SDK 当前的主要问题不是功能不足，而是部分模块仍依赖进程级全局状态或 legacy adapter 语义，与“可嵌入、多 Agent、folder-first、完整 runtime”的目标不完全一致。

重点问题分为四类：

1. **实例隔离不足**：Skill、MCP、后台任务和部分路径/权限服务仍存在全局状态。
2. **工具协议校验不完整**：原始 JSON Schema、非法 JSON、模型终止原因和权限恢复路径缺少统一契约。
3. **取消与生命周期不完整**：timeout、abort、SSE 断开及子 Agent cleanup 不保证底层任务已经停止。
4. **SDK 打包存在应用级副作用**：顶层 `sitecustomize`、导入时配置迁移、总量 requirements 和 legacy wheel 表面不利于干净嵌入。

建议优先处理工具调用正确性、MCP、后台任务隔离，再处理上下文、权限和打包维护。

---

## 2. P0：建议立即修复

### 2.1 原始 JSON Schema 未被严格校验

**相关代码**

- `floodmind/agent/native/native_flood_agent.py`
- `floodmind/agent/runtime/services/tool_execution_service.py`

**已观察到的错误**

```text
NativeFloodAgent._handle_create_plan()
got an unexpected keyword argument 'item'
```

当前只有 Pydantic `args_schema` 能可靠校验参数。仅声明 `ToolSpec.parameters` 原始 JSON Schema 的工具，未知字段、必填字段、类型、嵌套结构及 `additionalProperties` 没有被完整验证，参数最终直接进入：

```python
tool.func(**validated_args)
```

该问题不仅影响 `create_plan`，还影响所有只声明原始 JSON Schema 的工具。

**建议**

在 `ToolExecutionService` 中统一执行 JSON Schema 校验，顺序固定为：

```text
参数键名清洗
→ 工具参数 JSON 解析检查
→ JSON Schema 校验
→ Pydantic/自定义校验
→ 权限判断
→ 工具执行
```

未知字段或类型错误应返回 `INPUT_VALIDATION_FAILED`，不得调用 handler。增加 `create_plan(item=...)` 回归测试。

### 2.2 非法工具参数 JSON 被转换为 `{}` 后仍可能执行

**相关代码**

- `floodmind/agent/native/model_client.py`
- `floodmind/agent/native/executor.py`

模型生成的工具参数 JSON 解析失败后，当前实现可能将参数退化为 `{}`，继续发出 `tool_call_done` 并执行工具。对具有默认参数的写操作，这可能造成错误副作用。

**建议**

只要模型输出了非空参数，但 JSON 解析失败，就必须：

```text
禁止执行工具
→ 返回 TOOL_ARGUMENT_PARSE_ERROR
→ 要求模型重新生成合法 JSON
```

不能将“非法参数”解释为“无参数”。

### 2.3 模型终止原因未被完整处理

**相关代码**

- `floodmind/agent/native/model_client.py`
- `floodmind/agent/native/executor.py`

当前主要特殊处理 `finish_reason == "tool_calls"`，但没有完整区分：

- 正常结束；
- `length` / `max_tokens`；
- `content_filter`；
- refusal；
- provider-specific pause；
- 不完整 tool use。

因此被截断、过滤或拒绝的内容可能被持久化为正常完成结果。

**建议**

Provider Pipeline 输出统一终止枚举，例如：

```python
class TerminalReason(Enum):
    COMPLETED = "completed"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    FILTERED = "filtered"
    REFUSED = "refused"
    PAUSED = "paused"
    ERROR = "error"
```

只有 `COMPLETED` 才能提交最终答案。其他状态应触发续写、重试或结构化错误，不能记录为正常完成。

### 2.4 权限批准后的工具调用绕过正常 journal/memory 链路

**相关代码**

- `floodmind/agent/native/executor.py`

普通工具调用会经过 execution journal、round record、memory、artifact 和失败追踪；权限 ASK 获得批准后的路径却存在独立执行分支，可能只追加即时结果。

这会造成已批准的 `Write` 或 `Bash` 虽已执行，但持久化记忆与 journal 中缺少调用记录，恢复后可能重复执行。

**建议**

权限批准后重新进入统一 awaiting-tool 流程，或抽取唯一的 `_process_tool_result()`，确保普通调用和批准调用使用相同的执行、记录和产物处理链路。

### 2.5 MCP SSE 使用非流式 GET

**相关代码**

- `floodmind/agent/mcp_client.py`

普通 `httpx.Client.get()` 默认等待响应体读取完成，而 SSE 响应通常长期保持连接。即使 Server 已发出 endpoint 事件，SDK 仍可能等待到 read timeout，导致 MCP 工具无法注册。

**建议**

改用：

```python
with client.stream("GET", url, ...) as response:
    for line in response.iter_lines():
        ...
        if endpoint_received:
            break
```

增加独立的发现超时，并测试“发出 endpoint 后连接不关闭”的标准 SSE 场景。

### 2.6 MCP stdio 未按 JSON-RPC ID 分发响应

**相关代码**

- `floodmind/agent/mcp_client.py`

当前实现写请求后读取下一行作为响应，没有可靠校验 `id`、忽略 notification、处理并发响应或提供墙钟读取超时。连接锁只保护 ID 分配，没有覆盖完整写入和读取事务。

**建议**

短期：

- 对每个 stdio connection 加事务锁；
- lock 覆盖 write、flush、读取及 response ID 匹配；
- 忽略 notification 和不匹配的 ID；
- 增加超时和进程存活检查；
- 暂时将 stdio MCP 工具标记为非并发安全。

长期：采用单独 reader thread，将 response 按 JSON-RPC ID 分发到对应 Future，并将 notification 投递到独立事件通道。

### 2.7 MCP Pool 为进程全局对象，可能跨 Agent 串连接

**相关代码**

- `floodmind/agent/mcp_client.py`
- `floodmind/agent/native/native_flood_agent.py`

工具 Registry 是 Agent 实例级，但 MCP Pool 是全局的，并按 Server 名称保存连接。两个会话使用同名 Server、不同 URL 或凭证时，后连接者可能覆盖先连接者，导致跨会话调用、断连和工具污染。

**建议**

将 MCP Pool 绑定到 Agent/runtime：

```python
NativeFloodAgent(..., mcp_pool=McpClientPool())
```

如需共享连接，应增加 owner/session namespace、同名配置冲突检查、引用计数，并让工具函数绑定具体 connection，而不是执行时按名称查询全局 Pool。

### 2.8 后台任务订阅导致 Agent 泄漏和跨会话事件投递

**相关代码**

- `floodmind/agent/runtime/services/background_task_service.py`
- `floodmind/agent/native/native_flood_agent.py`

每个 Agent 向全局后台服务注册 bound callback，但没有 unsubscribe；服务强引用 callback，Agent 无法释放。回调也没有严格按 `task.session_id` 过滤，其他会话可能收到任务完成事件、命令信息和输出路径。

**建议**

让 `subscribe()` 返回取消函数或 token：

```python
unsubscribe = service.subscribe(
    callback,
    session_id=self.session_id,
)
```

Agent cleanup 中必须调用 `unsubscribe()`，服务投递前必须匹配 session ID。弱引用只能作为补充，不能替代显式生命周期。

---

## 3. P1：建议第二批修复

### 3.1 注入模型仍有路径使用全局模型 context window

**相关代码**

- `floodmind/agent/native/native_flood_agent.py`
- `floodmind/memory/dual_memory.py`

Executor 已通过 `_resolve_context_window()` 跟随当前注入模型，但历史注入/Memory 压缩路径仍可能直接读取全局 `settings.model.context_window`。

**影响**

注入模型与全局激活模型窗口不同时，会出现过早压缩或超过实际上下文窗口。

**建议**

所有上下文预算统一使用当前 Agent 的 `_resolve_context_window()`，runtime 内不得再次直接读取全局激活模型窗口。补充“全局 32K、注入 1M”及反向场景测试。

### 3.2 Context Compressor 可能跨重建历史复用旧摘要

**相关代码**

- `floodmind/agent/native/context_compressor.py`
- `floodmind/agent/native/executor.py`
- `floodmind/agent/native/native_flood_agent.py`

`_last_summary` 绑定 compressor 实例，仅 session ID 改变时重置。但同一 session 的后续顶层 stream 会从 memory 重建消息，旧摘要可能对应不同或重叠的历史片段。

**建议**

摘要状态必须记录覆盖的消息 ID、历史范围或 source digest。只有新历史确实延续同一已压缩前缀时才可增量复用；独立重建历史时需要重置或重新对齐。

### 3.3 Context Compressor 丢失结构化 tool call 信息

当前摘要主要读取文本 `content`，但工具名称、参数及关联 ID 通常存在于 `tool_calls` 和 `tool_result` 结构中。压缩后可能丢失站点、时间窗、文件路径和分析参数。Token 估算也忽略 tool arguments、reasoning、图片和结构化开销。

**建议**

摘要输入包含有界结构化信息：

```text
ToolCall: query_station
Arguments: {"station":"...","begin":"..."}
ToolResult: call_id=...
```

Token 估算至少基于完整消息序列化结果，不能只统计 `content`。

### 3.4 子 Agent 后台进程可能在子 Agent 结束后继续运行

**相关代码**

- `floodmind/tools/base_tools.py`
- `floodmind/agent/native/native_flood_agent.py`
- `floodmind/agent/runtime/services/sandbox_service.py`

子 Agent 使用 `sub-*` session ID，父 Agent cleanup 通常只清理父 session。子 Agent 启动的后台 Bash 可能在 sandbox 已销毁后继续运行。

**建议**

第一版可以禁止 subagent 使用后台 Bash；或者完整登记子会话后台任务，并在销毁 sandbox 前调用：

```python
background_tasks.kill_session(sub_session_id)
process_sandbox.kill_session(sub_session_id)
```

### 3.5 ParallelTask abort 不能真正停止已运行子 Agent

`future.cancel()` 不能终止正在运行的线程，`ThreadPoolExecutor` 退出时默认还会等待线程完成。DAG 分支也缺少足够的 abort 检查。

**建议**

- abort token 传入子 Agent stream 和工具调用；
- owned process/sandbox 支持 kill；
- DAG polling 增加 abort 检查；
- abort 时避免继续 `shutdown(wait=True)`；
- 仅在底层已停止后报告“已终止”。

### 3.6 工具 timeout 不会终止底层操作

**相关代码**

- `floodmind/agent/runtime/services/tool_execution_service.py`

线程 Future timeout 只表示调用方停止等待，底层线程仍可能继续执行。Agent 收到 timeout 后重试，可能与原操作并发产生副作用。

**建议**

- 只读操作支持 cancellation token；
- 副作用长任务使用可终止子进程；
- timeout 状态标记为 `INDETERMINATE`，不得暗示已取消；
- 未确认原任务状态前禁止自动重试写操作。

### 3.7 Planning 数据结构缺少完整规范化

**相关代码**

- `floodmind/agent/native/native_flood_agent.py`
- `floodmind/agent/native/types.py`

已确认问题包括：

- `update_step` Schema 声明 `needs`，handler 却未完整处理；
- `needs` 是字符串时可能按字符迭代；
- 允许重复 step ID；
- 可传任意初始 status；
- 独立节点拓扑顺序可能不稳定；
- `create_plan` 被标为 readonly，但实际修改 runtime state。

**建议**

计划安装前统一规范化：

```text
step ID 唯一
status 强制 pending
needs 必须为 list[str]
依赖必须存在
禁止自依赖
执行 cycle detection
保持原始插入顺序
```

更新依赖时应事务式验证，失败则回滚。`create_plan` 应标为 `state_write`，通过明确的 planning-control 例外允许在 plan mode 使用。

### 3.8 SSE 连接状态与 Agent 运行状态混用

**相关代码**

- `floodmind/server/routes/chat.py`

当前 flag 同时表示 SSE 连接存在和 Agent 正在执行。客户端断开或 SSE 到达生命周期上限后，flag 可能被移除，但后台 pump thread 仍在运行，随后同一 session 的第二个请求可进入同一个 Agent。

**建议**

拆分：

```text
connection_active
run_active
```

只有 pump thread 真正退出时才能清除 `run_active`。连接断开应发送 abort；底层未停止前拒绝同 session 新回合。

---

## 4. Provider 与公共 API

### 4.1 SDK 声明 Anthropic 直连，但实现仍使用 OpenAI Chat Completions

**相关代码**

- `floodmind/config/provider_registry.py`
- `floodmind/agent/native/model_client.py`
- `floodmind/agent/native/providers/`

Provider Registry 提供官方 Anthropic URL 和 Claude 模型，但 `ModelClient` 仍构造 `openai.OpenAI` 并调用 `chat.completions.create()`。官方 Anthropic 接口使用 `/v1/messages`，消息、工具、流式事件、stop reason、usage 和错误协议均不同。

**建议**

二选一：

1. 暂时删除官方 Anthropic 直连声明，要求通过 OpenAI-compatible gateway；
2. 实现正式 Anthropic transport，而不仅是参数 Pipeline，包括 Messages API、tool use/result、流式事件、终止原因、usage 和错误转换。

### 4.2 动态 MCP 加载结果返回不可调用名称

真实 MCP 工具名经过 sanitize，例如：

```text
mcp_hydro-rag_search_docs
```

但动态加载结果可能提示模型调用冒号格式名称。该名称未注册，也可能不符合 Provider function name 约束。

**建议**

动态加载结果直接返回每个 `ToolSpec.name`，注册后重建工具 catalog/system prompt，并明确 progressive 模式中新工具是“已加载”还是“仅进入目录”。

### 4.3 MCP inputSchema 被削弱

接入层只保留 `type`、`properties`、`required`，可能丢失 `additionalProperties`、`$defs`、`$ref`、`oneOf`、`anyOf` 等约束。

**建议**

Registry 中保留 MCP Server 提供的完整权威 Schema。若某 Provider 不支持部分结构，只在 Provider schema adapter 中做兼容转换，不应在 MCP 接入层永久丢失。

### 4.4 默认 `session_id` 固定为 `sdk-agent`

多个未显式传 ID 的 Agent 会共享 artifact、checkpoint、journal、后台任务和权限命名空间。

**建议**

空 ID 时生成唯一值：

```python
session_id = f"sdk-{uuid.uuid4().hex}"
```

固定的 `sdk-agent` 仅在调用方显式传入时使用。可先发一个版本的兼容 warning，再修改默认行为。

### 4.5 未提供 Workspace 时，进程 CWD 静默成为文件权限边界

嵌入宿主可能从 home、部署目录或 monorepo 根启动，该目录会被静默授权给模型。

**建议**

至少记录显著 warning；更安全的默认值是独立 app-data workspace。需要文件能力的宿主应显式传入 `Workspace`。

---

## 5. 安全边界与权限

### 5.1 动态 PowerShell 写目标选择 fail-open

**相关代码**

- `floodmind/agent/runtime/services/exec_write_scanner.py`
- `floodmind/agent/runtime/services/permission_service.py`
- `floodmind/tools/base_tools.py`

例如：

```powershell
$p = "C:\Users\User\Desktop\outside.txt"
Set-Content -Path $p -Value "x"
```

静态扫描器无法解析变量中的目标路径，可能选择放行。

**建议**

短期：识别到写命令但目标无法静态解析时返回 `ASK`，无交互宿主中返回 `DENY`，不得 fail-open。

长期：Workspace 边界依赖 OS/process/filesystem sandbox，命令扫描仅作为防御层，不能作为唯一安全隔离。

### 5.2 `permission_handler` 与 `permission_decision_hook` 文档区分不清

当前语义：

- `permission_handler=True`：宿主最高裁决，会跳过 SDK 权限判断；
- `permission_decision_hook`：只能收紧 SDK 的结果。

集成方可能误以为二者都不能放宽 SDK DENY，从而使用全量 `return True` 关闭安全护栏。

**建议**

在参数表、示例和安全章节显著标明：

```text
permission_handler=True 是 trusted-host override，
会覆盖 SDK DENY，仅限宿主已经独立完成全部安全裁决。
```

### 5.3 Folder-first 合法路径可能被 legacy 路径规则改写

**相关代码**

- `floodmind/agent/runtime/services/path_service.py`

项目中的合法路径：

```text
project/data/sessions/demo/outputs/result.json
```

可能被 legacy Web 兼容逻辑剥离为另一个相对路径。

**建议**

Legacy prefix rewriting 只能在 `workspace.mode == "web_session"` 时执行，并且必须匹配当前 session 的真实 legacy 输出路径。

### 5.4 Path/Permission 服务仍有进程级替换风险

部分 runtime service 仍通过全局 setter 或共享实例配置，不同 Agent 的构造顺序可能影响服务行为。

**建议**

逐步将 PathService、PermissionService 和相关 runtime service 注入到 Agent execution context；全局 setter 仅保留为 legacy adapter 或测试兼容入口。

---

## 6. 打包与部署

### 6.1 顶层 `sitecustomize` 污染整个 Python 环境

**相关代码**

- `pyproject.toml`
- `sitecustomize.py`

FloodMind wheel 安装顶层 `sitecustomize.py`。Python 启动时会自动导入它，即使应用没有 import FloodMind。该文件会加载 Matplotlib、修改字体和 DPI、替换 `findfont` 并抑制 warning，影响环境中的所有应用。

**建议**

从包元数据移除顶层 `sitecustomize`，改成显式初始化：

```python
from floodmind.plotting import configure_plotting
configure_plotting()
```

仅由 FloodMind 绘图路径主动调用。

### 6.2 Linux Docker 安装 Windows-only 依赖

**相关代码**

- `Dockerfile`
- `requirements.txt`

Linux 镜像无条件安装 `pywin32` 和 `comtypes`，可能导致构建失败。

**建议**

增加平台 marker：

```text
pywin32>=306; sys_platform == "win32"
comtypes>=1.4.0; sys_platform == "win32"
```

Docker 应优先安装明确的 server extra，而不是总量 requirements。

### 6.3 `requirements.txt` 与 core-only 文档不一致

当前 requirements 同时包含 GPU/ML、文档、OpenCV、Windows 和测试依赖，与核心 SDK 安装路线不一致。

**建议**

拆分 requirements，或完全以 `pyproject.toml` extras 为准：

```bash
pip install .
pip install ".[doc]"
pip install ".[gpu]"
pip install ".[legacy]"
pip install ".[dev]"
```

### 6.4 Web/TUI extras 与 wheel 内容不一致

extras 暴露 Web/TUI/legacy 依赖，但 package discovery 排除了相关模块，源码树测试可能通过，安装 wheel 后模块却不存在。

**建议**

选择一种明确策略：

1. wheel 包含这些模块；
2. legacy adapter 拆成独立 distribution；
3. 删除不再支持的 extras。

增加真实 wheel 安装测试：构建 wheel、安装到临时环境、导入每个公开 optional surface。

### 6.5 Docker 生产入口未启动 session cleanup

cleanup thread 仅在 `web_server.py` 作为 `__main__` 执行时启动，但 Docker 通过 Waitress import `web_server:app`，不会进入 main block。

**建议**

将 cleanup 启停放入 app factory/server lifecycle，由明确的进程 owner 管理，不依赖模块执行方式。

### 6.6 包元数据存在缺失资源

- `MANIFEST.in` 引用缺失的 `LICENSE`；
- README 引用缺失图片；
- `pyproject.toml` 缺少明确 license metadata。

应补充许可证和资源，或删除失效引用。

### 6.7 版本号重复硬编码

版本号在 package、CLI 和 legacy UI 多处重复，后续发布可能显示不一致。

**建议**

统一使用：

```python
from floodmind import __version__
```

或：

```python
importlib.metadata.version("floodmind")
```

---

## 7. 配置与可观测性

### 7.1 Tracing 路径不跟随 Workspace

**相关代码**

- `floodmind/agent/runtime/services/tracing_service.py`
- `floodmind/agent/native/native_flood_agent.py`

默认 trace 使用 `Path.cwd()/data/sessions`，不符合 folder-first 状态应放入 `<workspace>/.floodmind/` 的约定。公共 `Agent` API 也缺少稳定的 tracing 配置入口，写失败主要依赖日志发现。

**建议**

- trace 默认放入当前 Workspace state root；
- `Agent` 暴露 `tracing_service` 或 `tracing_config`；
- 写入返回明确 success/error；
- 增加 retention、redaction 和敏感数据说明；
- 写失败发 runtime event 或 metric。

### 7.2 导入 SDK 可能创建或迁移用户配置

**相关代码**

- `floodmind/config/settings.py`
- `floodmind/agent/native/native_flood_agent.py`

模块级 `Settings()` 可能创建、备份或重写 `~/.floodmind` 下的配置，多进程还可能并发修改。

**建议**

配置加载惰性化，普通 load 默认只读。初始化和迁移通过显式 API 或 CLI 执行：

```python
initialize_floodmind_home()
migrate_settings()
```

---

## 8. Skill 体系维护摘要

Skill 体系的详细修改方案应单独实施，核心原则如下：

1. `Agent` 增加公开的 `skill_roots` 和 `skill_writable_root`；
2. SkillRegistry 绑定 Agent 实例，不能通过进程级单例承载不同宿主项目；
3. `GetSkill`、List/CRUD/Refresh、catalog 和 SkillCurator 使用同一个实例 Registry；
4. Skill 内容缓存、Curator 状态和 writable root 按 Agent/宿主隔离；
5. Skill roots 自动进入当前 runtime 的只读范围，但不能自动给普通 Bash/Write 增加写权限；
6. 默认全局 Registry 和编程式 `register_skill()` 保持向后兼容；
7. LS_Agent 等宿主通过标准 `SKILL.md` 包管理业务 Skill，不复制到 SDK 仓库，不修改私有单例。

Skill 实例化改造可与 MCP、后台任务和 Path/Permission 的全局状态治理统一规划。

---

## 9. 推荐维护批次

### 批次 A：工具调用与状态正确性

1. 原始 JSON Schema 校验；
2. `create_plan(item=...)` 回归；
3. 非法工具参数 JSON 禁止执行；
4. stop reason 统一建模；
5. 权限批准路径统一 journal/memory；
6. Planning 数据规范化。

### 批次 B：MCP 重构

1. SSE stream discovery；
2. stdio JSON-RPC ID correlation；
3. notification、超时和进程存活；
4. Agent/session scoped Pool；
5. 动态工具真实名称及 catalog 刷新；
6. 保留完整 inputSchema；
7. connection ownership 和 cleanup。

### 批次 C：后台任务、取消和多会话

1. subscription unsubscribe；
2. session filter；
3. 子 Agent 后台进程生命周期；
4. ParallelTask cooperative abort；
5. timeout indeterminate 状态；
6. SSE run-active 与 connection-active 分离；
7. 默认 session ID 唯一化。

### 批次 D：上下文与 Provider

1. 所有路径使用注入模型 context window；
2. compressor summary 与历史范围绑定；
3. 保留 tool call/result 结构；
4. 改善 token 估算；
5. Anthropic 支持声明与实际 transport 对齐；
6. Provider stop/error 统一结构。

### 批次 E：安全与嵌入边界

1. 动态 PowerShell 写目标 fail-closed；
2. permission hooks 文档；
3. Folder-first legacy 路径改写；
4. 显式 Workspace 或安全默认；
5. Path/Permission 服务按 Agent 实例化；
6. Skill Registry 宿主根与实例隔离。

### 批次 F：打包与运维

1. 删除全局 `sitecustomize`；
2. requirements 分层；
3. Windows dependency markers；
4. wheel optional surface 一致性；
5. Docker cleanup 生命周期；
6. tracing 跟随 Workspace；
7. 配置初始化惰性化；
8. 版本、许可证和资源元数据统一。

---

## 10. 建议验收标准

### 工具协议

- 原始 JSON Schema 的必填、类型、未知字段和嵌套约束均生效；
- 非法工具 JSON 永不执行；
- `create_plan(item=...)` 返回输入校验错误；
- 截断、过滤和拒绝不会被记录为正常完成；
- 权限批准后的工具调用完整进入 journal、memory 和 artifact 链路。

### MCP

- 标准长连接 SSE Server 能完成 endpoint 发现；
- stdio 并发调用不会串响应；
- notification 不会被误认为 response；
- 不同 Agent 的同名 MCP Server 不会串连接；
- 动态加载返回的工具名可以直接调用；
- MCP schema 不在接入层丢失。

### 生命周期与隔离

- Agent cleanup 后无残留后台订阅；
- Session A 不会收到 Session B 的后台事件；
- 子 Agent 结束后无遗留进程；
- abort 后底层运行能够真正停止或明确报告未停止；
- 多个默认 Agent 不共享 session namespace；
- 同一 session 不会并发运行两个回合。

### 上下文与 Provider

- Memory 与 Executor 使用同一个当前模型窗口；
- 压缩摘要不跨不一致历史复用；
- 工具名称、参数及 call/result 关联在压缩后仍保留；
- 官方 Provider 声明与实际 transport 能力一致。

### 打包与运维

- 安装 FloodMind 不会通过 `sitecustomize` 修改无关 Python 进程；
- Linux Docker 不安装 Windows-only 包；
- wheel 中包含所有对外宣称的 optional surface，或不再宣称；
- Waitress/Docker 启动路径能启动 session cleanup；
- tracing 跟随 Workspace，失败可观测；
- import SDK 不会隐式迁移配置；
- package、CLI 和 UI 版本一致。

---

## 11. 建议的最小近期发布范围

如果下一版本无法一次完成全部维护，建议至少交付：

1. JSON Schema 中央校验和非法工具 JSON 阻断；
2. `create_plan` 参数与数据结构修复；
3. stop reason 正确处理；
4. MCP SSE、stdio ID correlation 和 Pool 隔离；
5. 后台任务 unsubscribe 与 session filter；
6. 权限批准路径统一记录；
7. 当前模型 context window 全链路统一；
8. 宿主 Skill roots 与 Agent 实例 Registry；
9. 移除顶层 `sitecustomize`；
10. Linux 依赖 marker 和 wheel 安装测试。

完成这些项目后，SDK 才能较稳定地支撑 LS_Agent 一类完整 runtime、多会话、MCP 与持久业务 Skill 的嵌入式应用。
