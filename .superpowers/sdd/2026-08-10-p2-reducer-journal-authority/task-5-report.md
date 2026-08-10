# Task 5 Report: RuntimeContext Injection

## Status

DONE

## Files changed

Production wiring:
- `floodmind/tools/session_context.py` — added `set_runtime_context` / `get_runtime_context`; session-context injection now carries `runtime_context`.
- `floodmind/agent/runtime/contracts/tools.py` — permission-policy checks read the injected `RuntimeContext.permission_service` and fail closed when unavailable.
- `floodmind/agent/runtime/services/permission_service.py` — removed the permission ContextVar/global getter/setter/reset API and all lazy PathService getter lookups.
- `floodmind/agent/runtime/services/path_service.py` — removed the path ContextVar/default singleton getter/setter/reset API.
- `floodmind/agent/runtime/services/background_task_service.py` — removed the process-wide background-service singleton getter/setter API.
- `floodmind/agent/runtime/services/tool_execution_service.py` — removed per-call service binding/reset and forwards `context.runtime_context` through session-context injection.
- `floodmind/agent/runtime/services/__init__.py` — removed exports for deleted service getters/setters.
- `floodmind/tools/agent_tool.py` — path resolution reads `RuntimeContext.path_service`; missing injection fails closed.
- `floodmind/tools/base_tools.py` — background tools read `RuntimeContext.background_service`; missing injection returns an explicit unavailable error.
- `floodmind/agent/native/native_flood_agent.py` — creates a per-run frozen `RuntimeContext`, passes it through `RunContext`, and explicitly owns/injects background services for main and specialist execution.
- `floodmind/agent/native/executor.py` — removed background-service global fallback.
- `floodmind/agent/native/types.py` — added the `runtime_context` field to `RunContext`.

Tests added/rewritten:
- `tests/test_runtime_context_injection.py`
- `tests/test_agent_skill_roots.py`
- `tests/test_apply_patch_permissions.py`
- `tests/test_background_task_service.py`
- `tests/test_bash_workspace_policy.py`
- `tests/test_file_tools_permissions.py`
- `tests/test_path_service_workspace.py`
- `tests/test_permission_host_fixes.py`
- `tests/test_sandbox_service.py`
- `tests/test_specialist_execution.py`
- `tests/test_tool_loading.py`

## Deleted legacy state

Deleted all three legacy service access paths and their storage:
- `get_permission_service`, `set_permission_service`, `reset_permission_service`, `set_global_permission_service`, permission ContextVar, and global fallback.
- `get_path_service`, `set_path_service`, `reset_path_service`, path ContextVar, and lazy default singleton.
- `get_background_task_service`, `set_background_task_service`, and process-wide singleton state.

## Audit

Repository production-source grep for all deleted getter/setter/reset names returned no matches.
The runtime audit test imports each service module and confirms the banned getters are absent.

## TDD evidence

Initial targeted run:
- Command: `python -m pytest tests/test_runtime_context_injection.py -v`
- Result: collection failed as expected with `ImportError: cannot import name 'get_runtime_context'`.

Passing targeted run:
- Command: `python -m pytest tests/test_runtime_context_injection.py -v`
- Result: `3 passed`.

Affected-test run:
- Command: targeted Task 5 affected test set
- Result: `107 passed`.

## Full suite

- Command: `python -m pytest -q`
- Result: `946 passed, 1 skipped` in 52.22 seconds.
- `git diff --check`: passed; only existing Windows LF/CRLF conversion warnings were emitted.

## Commit

Commit SHA: `211512d` (report added in this commit; the SHA is recorded here immediately afterward via amend).

## Concerns

- The working tree already contained extensive unrelated uncommitted changes before Task 5. Only explicit Task 5 paths are staged for this commit; unrelated paths remain unstaged.
- Several Task 5 target files also had pre-existing modifications, so path-level staging necessarily includes the current versions of those target files as directed by the brief.
