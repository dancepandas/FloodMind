# Changelog

All notable changes to FloodMind are documented in this file.

## [1.1.6] - 2026-08-06

### Changed

- **`SearchTools` 输出改为目录风格**：只列出工具名 + 基本描述（`- \`Bash\` [destructive]：要执行的 shell 命令`），去掉 `required=[...]`、`match=...`、`not-loaded` 等噪音；具体参数、required 与用法统一由 `GetTool(tool_name=...)` 返回。这样模型先通过搜索确定工具、再 `GetTool` 拿参数，不再需要猜工具。
- **`short_description` 剥离参数提示前缀**：`[必填] command: 要执行的 shell 命令。` 这类描述在目录/搜索结果中现在显示为 `要执行的 shell 命令`（剥离 `[必填]/[可选] xxx:` 前缀），让「基本描述」直接读起来像「这个工具是什么」，同时作用于 progressive 系统提示工具目录与 SearchTools 结果。

### Verification

- Full core-only test suite: `600 passed, 1 skipped`.
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
