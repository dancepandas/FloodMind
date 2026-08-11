# Changelog

All notable changes to FloodMind are documented in this file.

## [2.0.0] - 2026-08-11

> 重大版本：按 `FM_ARCHITECTURE_BASELINE.md` 完成 **forward-only 架构迁移（P0–P8）**。不向后兼容、不 fallback、不保留 legacy adapter，直接落 TARGET 契约。

### Added（TARGET 架构交付）

- **Canonical Event Journal + Deterministic Reducer**：JSONL Journal 为唯一运行事实源；确定性 `reduce(state, event)` 派生状态；`_turns` 变为 Reducer 派生投影，旧 `chat_history.json` 历史源下线且无读取回退。
- **身份层级**：`conversation_id / task_id / run_id / thread_id / turn_id / attempt_id / call_id / transaction_id / artifact_id / checkpoint_id`（§3.1）。
- **Tool Transaction + Approval Fingerprint + 幂等**：`proposed→…→succeeded/failed/denied/cancelled/indeterminate` 终态机，Pending Reconciliation，Checkpoint Resume（Journal Replay + Reconciliation）。
- **Model Layer 四层**：`ModelTransport → ProviderCodec → ResponsePipeline → ModelCapabilities`；Provider 原生块无损保存。
- **Context / Memory**：Projection Manifest、输入预算、Journal-backed Compact（Atomic Groups + Summary Event）、Soul/Core/AGENTS 版本化 + Provenance。
- **Sandbox / Artifact / Background**：`SandboxBackend`（OS/Container 边界，Landlock fail-closed）、`ArtifactService`（内容寻址 + 原子发布）、Background Kill 验证链 + Restart PID Reconcile。
- **ChildThread Runtime**：`ChildThreadRuntime` 替换 ad-hoc Specialist —— quota（max_turns/max_tokens/wall_clock）、Typed `SubagentResult`、父取消树（验证式清理）、child background namespace、严格子集权限隔离、SandboxBackend 会话绑定。
- **标准 SDK 公共 API**：`Agent` 标准身份 + `events_after(sequence)`（Journal 派生 committed 事件，可重放/对账）+ `resume(checkpoint_id)`（ResumeService 真实路径）。
- **SQLite 派生 Journal 索引**：`SqliteJournalIndex` 可重建、count 完整性校验、跨线程安全；JSONL 仍为唯一权威（§18）。

### Removed

- **Web / TUI 前端**：`floodmind/server/`、`floodmind/tui/`、`web_server.py`、`start.py`、web 调度器与 web SSE 存储（`sync_events`）整体移除；CLI `web` / `serve` / `tui` 命令删除。
- **历史源 / legacy 适配层**：`chat_history.json` 读取回退、ContextVar 全局 getter 的 fallback、旧 `AgentTool→ToolSpec` 兼容、Shadow Journal 双写过渡。

### Verification

- v2.0.0 完整回归：**1154 passed, 1 skipped**（唯一 skipped = Linux Landlock 平台测试，Windows 环境跳过）。

## [1.2.0] - 2026-08-08

### Added

- **宿主项目 Skill roots 公共 API**：`Agent(skill_roots=[...], skill_writable_root=...)` 支持宿主显式部署一个或多个 `SKILL.md` 根，并通过公开的 `agent.skill_registry` 检查实例目录与解析结果。顶层新增稳定导出 `SkillRegistry`、`SkillRoot`、`create_skill_registry`。
- **每 Agent Skill 运行时隔离**：每个 Agent 构造独立 `SkillRegistry` 与 `SkillCurator`；实例绑定的 `GetSkill` 缓存、Curator 使用统计、TaskExperience 与状态路径不再共享。bare/full 均提供 catalog + `GetSkill`，full 仅向 orchestrator 追加 CRUD，specialist 仍只有 `GetSkill`。

### Changed

- **发现优先级固定**：同名 Skill 按 `builtin > host > project > .claude > ephemeral` 选择；显式根在构造时规范化为绝对路径，后续切换 CWD 不改变含义。`workspace` 与 Skill roots 相互独立，运行时不会隐式扫描 workspace。
- **Skill 根默认只读**：发现根只加入运行时读授权，不会给普通 `Write` / `Edit` / `Bash` 增加写权限；只有 `skill_writable_root` 是 CRUD 写源。内置、只读根与 ephemeral Skill 不能 Update/Remove；CRUD 对 canonical path、symlink 与 containment 做约束检查。
- **全局 API 保持兼容**：`get_skill_registry()` / `register_skill()` 仍操作历史默认全局 Registry，并保留原状态路径与旧调用行为；Agent runtime 不再依赖该全局单例。
- **宿主集成边界**：LS_Agent 可把已部署的 `SKILL.md` 目录作为显式 `skill_roots` 传入 FloodMind，本仓库不修改 LS_Agent。

### Verification

- 版本与 CLI 定向测试已执行；v1.2.0 完整回归数量由发布主流程确认，不在此预填。

## [1.1.9] - 2026-08-06

> 注：无 v1.1.8——其内容（ContextCompressor 原子组 + context_window 跟随注入模型）经确认属 v1.1.7 的 MiniMax 2013 根因链，已并入 v1.1.7。

### Fixed

- **P0-1 `exec_bash` 子进程关闭 stdin（挂起主因）**：`_impl_exec_bash` 的 `Popen` 此前只设 stdout/stderr 管道，stdin 继承父进程。模型发出读标准输入的命令（裸 `python`、`python -`、交互式程序）时子进程永久等输入，直到 120s 默认超时才被 kill——实测一轮任务 Bash 挂起约 4 分钟，用户侧看就是"智能体没反应"。现设 `stdin=subprocess.DEVNULL`（一次性执行工具本就不支持交互输入）。
- **P0-2 Bash 工具描述告知 shell 类型 + stdin 已关闭**：描述只写"自动选择可用 shell"，Windows 上实际是 PowerShell，模型不知道就照写 bash 方言（`2>/dev/null`、heredoc）。新增 `_bash_shell_hint()` 动态带检测结果：`当前 shell：powershell（用 ; 连接、勿用 2>/dev/null、&&、heredoc）` + `stdin 已关闭，禁止交互式/读标准输入命令，Python 先写文件再执行`，接入 Bash 工具描述与 `ExecBashInput.command` 字段说明。
- **P1-1 完整模式注册宿主自定义 tools**：`tools` 参数此前只有 `_init_bare` 消费，完整路径 `_init_tools()` 只注册内置工具，宿主注入的业务工具被静默丢弃。现 `_init_tools()` 末尾（内置 + MCP 之后、`_init_executors` 快照 tools_schema 之前）补注册到 orchestrator 与 specialist 双 registry。
- **P1-2 完整模式保留宿主 system_prompt**：`system_prompt` 参数此前只在 `_init_bare` 使用，完整模式忽略。现 `__init__` 存 `_host_system_prompt`，`_init_executors` 与 `_rebuild_system_prompts` 都把它作为独立段注入——skill 热插拔重建提示词时宿主段不丢。
- **P2 未声明 permission_policy 回退 is_readonly**：`PermissionRequest` 新增 `is_readonly` 字段（ToolExecutionService 填充）。未显式声明 policy 的工具此前一刀切 DENY，宿主用 `build_agent_tool` 标了 `is_readonly=True` 仍被拒，接入成本高。现回退看 `is_readonly`：True 按只读放行，False 走 ASK/DENY。

### Added

- **后台任务（`Bash run_in_background=True`）**：长任务不再受同步 120s 超时限制，可异步跑完再由 Agent 感知。
  - `BackgroundTaskService`（`floodmind/agent/runtime/services/background_task_service.py`）：stdout/stderr 直写文件（无 PIPE 死锁风险），文件落 `.floodmind/sessions/<sid>/background/<task_id>/{out.log,err.log,meta.json}`；每任务 daemon wait 线程 → 完成队列 → subscribe 回调；Windows `taskkill /PID /T /F`、POSIX `killpg` 杀进程树。
  - `exec_bash` 新增 `run_in_background` 参数：走全部安全管线（危险命令/写目标/workdir/sandbox）后交服务托管，立即返回 `task_id` + 文件路径；同步路径零改动。显式 `timeout` 作为存活上限覆盖，默认 30 分钟兜底 kill。
  - 三个工具（完整 + bare 双模式注册）：`TaskOutput(task_id, tail_lines=200)` 只读查状态/输出尾部；`TaskList()` 只读列本会话任务；`TaskKill(task_id)` exec 策略杀进程树。
  - executor 完成通知注入：`_inject_background_notifications` 在每次 LLM 调用前 drain 本会话完成队列，以 user 角色消息（`[后台任务完成/失败] …`）追加 state.messages——与排队用户消息同通道，厂商兼容性最好。
  - 空闲唤醒：Agent 初始化订阅任务完成 → EventBus 发 `background_task_completed` 事件（运行中的 stream 带出，宿主 UI 实时可见）；宿主收到且无活跃回合时自行决定是否开新回合，SDK 不越权自发回合。`Agent.cleanup()` / `__del__` kill 本会话存活任务（meta.json 保留供审计）。
  - 护栏：单会话并发上限 8（可配置）、单任务最大存活 30 分钟兜底 kill、会话结束清理存活任务。

### Fixed

- **permission_handler 改为宿主最高裁决**：此前 `permission_handler` 返回 `True` 只表示"不拒绝"，SDK 的 permission_service 仍会继续判断（ASK/DENY 照常触发），web 宿主无法真正放行。现：`True` = 宿主显式放行 → 直接 ALLOW 并跳过 permission_service（宿主放行是最高权威）；`False` = 宿主拒绝 → DENY；`None`（或钩子异常） = 宿主无意见 → 交给 SDK 正常判断。符合文档承诺的"安全网关"语义。
- **ASK 无宿主响应时超时自动拒绝（不再无限卡死）**：executor `_on_awaiting_permission` 此前对未响应的 ASK 无限 `time.sleep(0.5)` 轮询，web 无人响应就永久挂起。现 `AskService` 新增 `age()`/`reject()`/`get_timeout()`，executor 在 ASK 等待超过配置超时（默认 300s，AskService 可配）后自动拒绝并回到 `awaiting_llm` 让模型处理，不再无限轮询。
- **Bash 写范围可配（uploads/ 等不再被路径网误拒）**：`Workspace` 新增 `add_writable_root()`/`add_readable_root()`（运行时扩展写/读白名单，幂等；PathService 持活引用即刻生效），宿主可放行 workspace 外目录（如 web 的 uploads/、web_workspace/）。`build_workspace`（web_session 模式）自动把会话目录（含 uploads/、outputs/）纳入写根，不依赖 sandbox_strategy。
- **后台任务 kill/失败状态变化立即通知 Agent**：`TaskKill`/`kill_session` 此前只改状态、不等 wait 线程，且 `_watch` 线程会把 "killed" 覆盖成 "failed"，Agent 无法感知任务被主动关闭。现 kill 先标记 killed 再杀进程、同步 `_finalize`（进完成队列 + 通知订阅者）；executor 注入通知区分 `[后台任务完成/失败/被终止]`。

### Verification

- Full core-only test suite: `633 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.7] - 2026-08-06

### Fixed

彻底修复 MiniMax 400 `tool id not found (2013)`。该错误有三层叠加根因，本版一并修复：

- **① Tool-call id 对齐**：流式解析中 `ToolCall` 与回传历史里的 assistant 消息对 `id` 使用了两套来源——构造 `ToolCall` 时 `acc["id"] or f"call_{idx}_{time.time_ns()}"` 生成 fallback id，而 `ProviderPipeline.build_assistant_message` 读原始 accumulator 的 `acc.get("id") or ""`。当 MiniMax 等厂商偶发在流里不发 tool call 的 `id`（或后到）时，历史 assistant 消息的 `tool_calls[].id` 为空、工具结果消息的 `tool_call_id` 却是 fallback id，二者对不上即被校验拒绝（工具本身执行成功）。现改为在构造 `ToolCall` 前把 fallback id **写回 `acc["id"]`**（两处：`finish_reason=="tool_calls"` 分支 + 流结束兜底分支），accumulator 成为唯一 id 来源，assistant 消息与工具结果的 id 永远一致；provider 给了非空 id 时原样保留。
- **② ContextCompressor 保持工具调用原子组（主因）**：此前 `compress()` 用 `head[:2] + tail[-4:]` 机械切分——当尾部 `tail_keep` 条恰好全是 tool 结果、声明它们的 `assistant(tool_calls)` 消息落在倒数第 `tail_keep+1` 条时，该 assistant 被切进 middle 摘要，留下孤儿 tool 消息；MiniMax 校验 tool 结果的 `tool_call_id` 找不到对应 assistant `tool_calls` 即 400。现新增 `_aligned_split_points()`：切分点若落在 `assistant(tool_calls) + 紧随 tool 结果` 原子组中间，前移到组首（tail 保留整组、head 把整组并入 middle），保证配对不被拆散。同时 head 至少保留到首条 user 消息，不再把用户最初需求切进摘要。
- **③ `context_window` 跟随注入模型（放大器）**：executor 此前硬编码 `settings.model.context_window`（全局默认模型，即 catalog 第一个，如 deepseek-v4-pro 131072），而非宿主注入 `ModelClient` 实际模型的窗口（如 MiniMax-M3 1M），导致压缩在本不该发生的体量就触发，放大结构破坏。现 `NativeFloodAgent._resolve_context_window()` 优先取注入模型 preset 的 `max_context_tokens`，查不到才回退全局默认。

### Verification

- Full core-only test suite: `607 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.6] - 2026-08-06

### Removed

- **移除 `SearchTools` 工具**：工具发现改为与 skill 完全一致的模型——`## 可用工具` 提示目录直接列出全部工具的名称与基本描述（模型无需搜索就知道有哪些可用），需要具体参数、required 与用法时调用 `GetTool(tool_name=...)` 查看并加载。此前 `SearchTools` 要求模型先凭空猜一个关键词再拿子集，模型对工具目录一无所知，只能瞎碰。移除后：
  - `DEFAULT_CORE_TOOLS` / `settings.tool_loading.core_tools` 默认只含 `GetTool`/`GetSkill`；
  - `make_search_tools_tool` 工厂删除，`NativeFloodAgent._register_tool_catalog_tools` 只注册 `GetTool`；
  - progressive 提示目录与未加载工具的错误提示不再引导「先调用 SearchTools」。

### Changed

- **移除工具输出的静默字符截断**（任务质量急转直下的根因）：此前两层截断会先于模型看到结果之前砍掉长工具输出——
  - `base_tools._finalize_tool_output` 对所有工具输出设 8000 字符硬上限，超长即截断为预览 + 文件指针；
  - `ExecutionJournalService.process_tool_result` 对超过 1000 字符的结果只回灌 800 字符摘要 + 归档指针，模型拿不到完整内容。
  现在两层均移除/改造：`_finalize_tool_output` 返回完整输出；`process_tool_result` 模型始终看到完整工具结果（长结果仍额外归档供 `JournalSearch`/`JournalGetFullResult` 回溯，但不再用摘要替换模型可见内容）。上下文上限由 token 级 `ContextCompressor` 兜底（超阈值才压缩中段、保留头部与最近轮次），而非字符数硬截断。
- **`short_description` 剥离参数提示前缀**：`[必填] command: 要执行的 shell 命令。` 这类描述在目录/提示中现在显示为 `要执行的 shell 命令`（剥离 `[必填]/[可选] xxx:` 前缀），让「基本描述」直接读起来像「这个工具是什么」，同时作用于 progressive 系统提示工具目录与 GetTool 结果。

### Verification

- Full core-only test suite: `601 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.5] - 2026-08-06

### Fixed

- **Tool-call argument key sanitization**: `ToolExecutionService` now normalizes model-generated argument key names (strip edge quotes/whitespace, strip intra-key control chars/quotes, drop empty keys) before permission checks, input validation, and execution. MiniMax-M3 and similar models occasionally emit malformed keys like `{"tool_name"": "..."}` (trailing quote); previously tools without a pydantic `args_schema` (`GetTool`/`SearchTools`, system tools, MCP tools) passed them straight into `**kwargs` and crashed with `TypeError: unexpected keyword argument 'tool_name"'`, which models could not self-correct. Sanitized keys now execute normally (or fail with a clear validation feedback). Defense-in-depth: `TOOL_EXECUTION_ERROR` feedback now explicitly hints "参数名可能有多余引号/空白" when the error is `unexpected keyword argument`.
- **exec command-body write-target enforcement**: new `floodmind/agent/runtime/services/exec_write_scanner.py` statically extracts high-confidence write targets from `exec_bash` command bodies (shell `>`/`>>` redirects; PowerShell `Set-Content`/`Add-Content`/`Out-File`/`New-Item`/`Copy-Item`/`Move-Item`/`Remove-Item`/`Set-Item`) and resolves each with `access="write"`; any target outside allowed writable roots is DENIED. This closes the "read-only authorization bypassed by Bash" hole. Wired into both `_impl_exec_bash` (all modes) and `PermissionService._check_exec_policy` (full runtime, hard-deny before the mutating-command ASK). Conservative by design: only absolute/qualified path-looking targets are checked, quoted string literals (`echo "x > y"`, echoed cmdlet text) are not misdetected, `/dev/null`/`NUL` are skipped, and unresolvable targets (e.g. variables) fail open for host tightening via `permission_decision_hook`.
- **folder-first read whitelist includes installed skill registry**: `PathService` now allows reading the `SkillRegistry` discovery roots plus `site-packages/skills` (separately installed skill packages), so agents can directly read installed skill source files (`SKILL.md`/`references/`/`scripts/`) in folder-first mode instead of hitting repeated "not in allowed dir" denials that cause retry death-loops. Read-only; writes are unaffected.
- **PathService read-deny reason now includes actionable guidance**: appended "如为工作区外文件，请先在工作区附件中引用该文件以完成授权".

### Verification

- Full core-only test suite: `598 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.4] - 2026-08-05

### Fixed

- LLM retry now also covers the `create()` connection-establishment stage. `is_retryable_error` recurses into the `__cause__`/`__context__` chain (e.g. `openai.APIConnectionError`'s `str()` is always "Connection error." but the real retryable cause such as "peer closed connection" lives in `__cause__`), and `ModelClient.stream_chat` re-raises retryable errors in all exception handlers (connection + mid-stream) so the original exception chain survives to the executor's retry loop. Non-retryable errors still emit error/timeout events as before.

### Verification

- Full core-only test suite: `573 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.3] - 2026-08-05

### Fixed

- LLM streaming disconnections are now retried: `is_retryable_error` recognizes `closed connection` / `chunked` / `remote protocol` / `peer closed` patterns (e.g. `httpx.RemoteProtocolError: peer closed connection` mid-chunked-read). The executor's existing retry loop already re-invokes `ModelClient.stream_chat` on raised errors and clears partial state, so a network blip no longer fails the whole agent round.

### Verification

- Full core-only test suite: `571 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.2] - 2026-08-05

### Fixed

- Reworded `CreateScheduledTask` tool description to make clear it schedules time-based dispatch only and is not for launching/backgrounding a process now; points the model to `Bash`/shell tools for immediate process execution. (The previous wording with 「后台」misled the model into selecting it for "run a background program" requests.)
- Fixed scheduled-task execution failing with "workspace unknown": `NativeFloodAgent._effective_workspace` now lazily creates a folder-first cwd workspace when neither an explicit workspace nor a contextvar workspace is available (e.g. scheduling runtime creating an agent via `create_flood_agent` without injection), matching the `Agent` wrapper default. Web contextvar-injected path is unchanged; creation failure stays fail-closed.

### Verification

- Full core-only test suite: `569 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.1] - 2026-08-05

### Fixed

- Bare mode (`Agent(bare=True)`) now auto-loads MCP servers configured in `mcp.json`, matching full-runtime behavior. Previously `_init_bare` short-circuited before the MCP block in `_init_tools`, so configured servers were never connected and `_mcp_pool` was never initialized.
- Extracted MCP auto-connect + tool registration into a shared `NativeFloodAgent._load_mcp_tools()` called from both `_init_bare` (before tool catalog registration) and `_init_tools`; failure is non-fatal (logged warning).
- Bare mode now loads skills too: shared `NativeFloodAgent._load_skills()` populates the skill catalog and registers `GetSkill` in both `_init_bare` and `_init_tools`; the bare orchestrator system prompt includes a `## 可用 skills` section. (Skill CRUD management tools remain full-runtime only.)
- Added an autouse test fixture defaulting `settings.mcp.servers` to empty so the SDK suite stays hermetic/portable and does not depend on machine-local MCP scripts.

### Verification

- Full core-only test suite: `567 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.0] - 2026-08-05

### Added

- Public `Agent` now supports `bare=False` to request the full NativeFloodAgent runtime (built-in tools, MCP, Skill, permission-ask events, workspace binding) instead of bare embedding only.
- Added compatibility proxies on public `Agent`: `agent.memory`, `agent.session_id`, `agent.clear_memory()`.
- `Agent.stream(msg, **kwargs)` now forwards extra kwargs (`abort_check` / `attachments` / `resume_session_id`) to the underlying runtime.
- MCP: `build_mcp_tool_specs` sanitizes model-visible tool names (`mcp:<server>:<tool>` → `mcp_<server>_<tool>`) for OpenAI-compatible endpoints; bound functions still call with the original colon-delimited full name.
- MCP: `McpClientConnection.is_connected` now checks stdio process liveness (`process.poll()`).
- MCP: `McpClientPool.call_health()` records the most recent per-server tool-call outcome (`ok` / truncated `error`), thread-safe.
- MCP: `McpClientPool.add_server_connected_listener` / `remove_server_connected_listener` notify hosts when a server connects (idempotent, non-blocking).
- `_build_model_info` prefers the host-routed `ModelClient.model_name` and falls back to the SDK default model resolution.

### Changed

- `_handle_disconnect_mcp_server` now cleans up tools using the sanitized name prefix (`mcp_tool_prefix`) so disconnect cleanup matches the sanitized registry keys.

### Verification

- Full core-only test suite: `563 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.0.2] - 2026-08-05

### Added

- Added host-level `permission_decision_hook` to the public `Agent`, `NativeFloodAgent` (bare and full runtime), and `ToolExecutionService`.
  - Signature: `permission_decision_hook(tool_name, tool_input, sdk_decision, permission_policy) -> PermissionDecision`.
  - Runs after the SDK's base permission decision; host can keep DENY/ASK, or upgrade ALLOW to ASK (interactive `permission_ask`) / DENY.
  - Monotonic guard: the hook can only tighten, never loosen, SDK security decisions (path/dangerous-command/sub-agent tier/planning hard gates cannot be bypassed).
  - Fail-safe: hook exceptions or invalid return values preserve the SDK's original decision.
  - Traces record the post-hook final decision so logs match behavior.
- Wired the global `AskService` into bare-mode `ToolExecutionService` so hook-upgraded ASK can run the `permission_ask` → respond flow in bare mode.
- Passed `permission_handler` through to full-runtime `ToolExecutionService` too, so `Agent(permission_handler=...)` behaves consistently in bare and full modes.

### Verification

- Full core-only test suite: `544 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.0.1] - 2026-08-04

### Added

- Added SDK-first folder workspace defaults: `Agent` now binds a folder-first workspace from the launch cwd when no explicit workspace is provided.
- Added `.floodmind/` managed layout for sessions, artifacts, tmp files, scripts, and sandboxes under the active workspace.
- Added SDK purity tests covering top-level import boundaries and core dependency metadata.
- Added neutral runtime adapter modules with legacy Flask/SSE shim modules kept as compatibility aliases.

### Changed

- Changed default dependency surface to SDK/core-only; Web/TUI dependencies live behind optional extras.
- Changed CLI Web/TUI commands to legacy notice-only behavior instead of starting old UI stacks.
- Changed file tools and Bash workspace handling to route path/cwd/workdir resolution through runtime path and permission services.
- Changed artifact watching to focus on the workspace artifact directory instead of treating the workspace root as generated output.

### Fixed

- Fixed recursive checkpoint file snapshots by making checkpoints state-only.
- Removed the file snapshot parameter from `CheckpointService.save()`; checkpoints now persist only `state.json` and `manifest.json`.
- Fixed legacy Web adapter tests so they skip in SDK/core-only environments without Flask.

### Verification

- Full core-only test suite: `532 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.
