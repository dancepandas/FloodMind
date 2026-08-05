# Changelog

All notable changes to FloodMind are documented in this file.

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
