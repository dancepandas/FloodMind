# tests — 单元与故障注入回归

pytest 测试集，覆盖组件行为与故障路径。与 `evals/` 分工：`tests/` 测组件与故障注入，`evals/` 测跨组件确定性轨迹。

## 主题分布

| 主题 | 代表文件 |
| --- | --- |
| SDK 与宿主集成 | `test_sdk_agent.py`、`test_sdk_purity.py`、`test_sdk_events.py` |
| 执行循环与预算 | `test_executor.py`、`test_context_compressor.py`、`test_token_budget.py`、`test_guardrails.py`、`test_handoff.py` |
| 模型层与 Provider | `test_model_client_*.py`、`test_model_resolver.py`、`test_provider_pipelines.py`、`test_codec.py`、`test_transport.py` |
| Canonical 契约 | `test_canonical_events.py`、`test_canonical_parts.py`、`test_run_state.py`、`test_tool_transaction.py` |
| Journal / Reducer / Checkpoint / Resume | `test_journal_*.py`、`test_reducer.py`、`test_checkpoint_*.py`、`test_resume_service.py` |
| 记忆 | `test_memory_*.py`、`test_semantic_store.py`、`test_core_memory_store.py`、`test_experience_tree.py` |
| 工具 / 权限 / 工作区 | `test_base_tools.py`、`test_tool_execution_service.py`、`test_permission_*.py`、`test_workspace.py` |
| Skill / MCP | `test_skill_*.py`、`test_mcp_*.py` |
| 产物 / 沙箱 / 后台 / 子线程 | `test_artifact_*.py`、`test_sandbox_*.py`、`test_background_*.py`、`test_child_*.py` |
| 可观测性与阶段验收 | `test_tracing_*.py`、`test_usage_accounting.py`、`test_p*_acceptance.py` |

## 运行

```bash
python -m pytest -q
python -m pytest tests/test_executor.py -k permission -q
```

当前基线：1601 passed, 2 skipped（Windows；skip 为平台专属用例）。

## 约定

- 测试保持 hermetic：不访问真实模型 API；外部 HTTP 用 monkeypatch；默认清空 MCP servers。
- 平台差异用 `skip` / `skipif` 表达，不删除平台用例。
- 测试状态根使用临时目录或 `FLOODMIND_HOME`，不得写入用户真实配置、记忆与 journal。
- 现有代码可能依赖真实文件的测试使用 `tmp_path`，不要改成仓库内固定路径。
