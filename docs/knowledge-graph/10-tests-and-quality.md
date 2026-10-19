# 10. 测试与质量门禁

> 代码位置：`tests/`、`evals/`、`.github/workflows/ci.yml`、`pyproject.toml`。统计基于 2026-10-19 工作区。

## 1. 总览

| 指标 | 数值 |
|---|---:|
| 测试文件 | 137 个 `.py`（136 个 `test_*.py` + `conftest.py`） |
| `def test_` 函数 | 1418 |
| 收集到的用例 | 1566（含 parametrize 展开） |
| 实际运行结果 | 1565 passed, 1 skipped |
| `parametrize` | 30 处 |
| 测试类 | 199 |
| 异步用例 | 0 |
| xfail | 0 |
| skipif 装饰器 | 3 处（Windows taskkill、Landlock、Windows junction） |
| 运行时 skip | 5 处（symlink 不可用：artifact 1、background 1、skill registry 3） |
| 自定义 marker | 无 |
| 根项目 CI | 有：GitHub Actions，Python 3.10/3.12 |

pytest 配置（`pyproject.toml:108-111`）：

```toml
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-v --tb=short"
```

运行：

```bash
python -m pytest -q
python -m pytest tests/test_executor.py -k "permission" -q
```

CI（`.github/workflows/ci.yml`）：

1. `python -m pip install -e . pytest`；
2. `python -m pytest -q`；
3. `python scripts/calibrate_judge.py --judge rule --min-agreement 1.0`（判定器确定性基线）；
4. `python -m evals.runner --json eval-report.json` 作为内部评测门禁（smoke/tool/regression/safety）；
5. 仅在 `schedule` / `workflow_dispatch` 时运行 `python -m evals.runner --heldout`；
6. 无论成败上传 `eval-report.json`、`heldout-report.json`。

## 2. 主题地图

| 主题 | 代表文件 | 覆盖重点 |
|---|---|---|
| SDK/API | `test_sdk_agent.py`（75 用例）、`test_sdk_purity.py`、`test_sdk_events.py`、`test_sdk_api_identity.py` | 会话隔离、bare/full 工具可见性、权限 hook、导入纯净性 |
| 执行核心 | `test_executor.py`、`test_context_compressor.py`、`test_retry_policy.py`、`test_atomic_groups.py`、`test_execution_plan.py` | 无工具完成、坏 JSON、最大迭代、DOOM、中断感知重试、压缩原子组 |
| 模型路由/回退 | `test_model_routing.py`（14）、`test_model_resolver.py`、`test_model_presets.py`、`test_capabilities.py` | 回退仅在未输出时发生、run 内粘滞、run 起点复位、子代理模型路由、能力解析 |
| Guardrail/Handoff | `test_guardrails.py`、`test_handoff.py` | 输入 tripwire 不调 LLM、输出重试、handoff 接管同一 run |
| Provider/Codec | `test_provider_pipelines.py`、`test_codec.py`、`test_response_pipeline.py`、`test_golden_fixtures.py` | 路由、请求翻译、流式 reasoning/usage/tool_call、空 choices |
| Runtime 契约 | `test_canonical_events.py`、`test_canonical_parts.py`、`test_tool_transaction.py`、`test_run_state.py`、`test_identity.py`、`test_runtime_context.py` | 枚举完整性、哈希确定性、状态迁移、contextvar |
| Reducer/Journal | `test_reducer.py`、`test_journal_writer.py`、`test_journal_authority.py`、`test_journal_index.py`、`test_journal_fencing.py`、`test_journal_compact.py`、`test_history_projection.py` | 确定性重放、重复 event_id、CAS、半写修复、lease fencing、索引重建 |
| Checkpoint/Resume | `test_checkpoint_service.py`、`test_checkpoint_cursor.py`、`test_checkpoint_serialization_hardening.py`、`test_resume_service.py` | 快照契约、cursor 绑定、lease fencing、版本不匹配 fail-closed |
| Memory | `test_memory_dual.py`、`test_dual_memory_journal.py`、`test_memory_services.py`、`test_memory_consolidation.py`、`test_memory_recall.py`、`test_memory_expiry.py`、`test_semantic_store.py`、`test_core_memory_store.py`、`test_experience_tree.py`、`test_task_experience.py`、`test_memory_tools.py`、`test_background_review.py` | Journal 投影、四类操作原子性、三因子召回、过期与 owner 隔离、项目级核心记忆 |
| 凭据/配置 | `test_secret_store.py`、`test_credential_env.py`、`test_settings.py`、`test_search_config.py`、`test_mcp_config_validation.py` | 静态加密、子进程凭据过滤、配置优先级、MCP 配置唯一契约 |
| Tools/权限 | `test_base_tools.py`、`test_file_tools.py`、`test_apply_patch_permissions.py`、`test_exec_write_scanner.py`、`test_tool_execution_service.py`、`test_permission_service.py`、`test_permission_tier.py`、`test_plan_mode_gate.py`、`test_tool_output_service.py` | 编码无损、越界拒绝、写目标扫描、幂等、planning 硬门、全量结果卸载 |
| Tools 加载 | `test_tool_loading.py`、`test_agent_tool.py` | progressive/eager 模式、按需加载、宿主工具注入 |
| Skills/MCP | `test_skill_registry.py`、`test_skill_curator.py`、`test_skill_md_writeback.py`、`test_mcp_client.py`、`test_mcp_agent_isolation.py` | 读写根、junction/symlink、CRUD 回读回滚、pool 生命周期、传输超时 |
| 产物/沙箱/后台/子线程 | `test_artifact_service.py`、`test_artifact_traversal.py`、`test_sandbox_backend.py`、`test_background_task_service.py`、`test_background_kill_chain.py`、`test_child_thread_*` | 哈希与防穿越、Landlock、并发上限、kill 验证、配额与取消清理 |
| 追踪/计划/成本 | `test_tracing_service.py`、`test_trace_processors.py`、`test_trace_metrics.py`、`test_usage_accounting.py`、`test_metrics_report.py`、`test_otlp_exporter.py`、`test_tracing_otlp.py`、`test_plan_update.py`、`test_plan_store.py`、`test_plan_persistence.py` | span 嵌套、processor 失败隔离、run/step 成本归因、OTLP 导出、计划环检测与跨 run 恢复 |
| 评测/判定器/收益基线 | `test_multi_agent_benchmark.py`（36）、`test_eval_case_packs.py`、`test_judge_calibration.py` | 指标聚合、退化判定、case pack 结构与 held-out、判定器一致率、离线默认不调用模型 |
| 知识图谱工具 | `test_knowledge_graph_viz.py` | 可视化载荷裁剪、内嵌 JSON 安全转义、离线无外链、报告落地 |
| 阶段验收 | `test_p2` ~ `test_p7_acceptance.py` | P2 重放、P3 指纹+幂等+resume、P5 压缩、P6/P7 隔离与清理 |

## 3. 测试夹具与隔离

| 夹具 | 位置 | 作用 |
|---|---|---|
| `mock_llm_service` | `tests/conftest.py` | MagicMock 模型 |
| `mock_tool_registry` | `tests/conftest.py` | 单 ToolSpec |
| MCP 隔离（autouse） | `tests/conftest.py` | 默认清空 `settings.mcp.servers` |
| Fake ModelClient | 多个测试文件 | `stream_chat` 返回脚本化事件 |
| Journal fake writer | `test_journal_authority.py` | 不落盘 |
| 环境隔离 | `test_settings.py`、`test_sdk_purity.py` | `FLOODMIND_HOME`、`HOME` 指向临时目录 |

测试整体是 hermetic 的：不访问真实模型 API，MCP HTTP 被 monkeypatch，`test_sdk_purity.py` 还断言导入前后 DNS/TLS 无变化。

## 4. 内部评测集（非 pytest）

`evals/` 用确定性脚本轨迹驱动真实 `NativeAgentExecutor`，分 `smoke`、`tool`、`regression`、`safety` 四层，常规集共 243 个用例，另有 10 个 held-out 校准用例：

- `tool` / `safety` 层加载真实 `Read`/`Write`/`Glob`/`Grep`/`Edit`，经真实 `ToolExecutionService` 与权限检查执行；路径服务只允许评测 workspace 内的读写。
- 手工高价值场景放在 `evals/cases/*.json`；系统性覆盖由 `evals/case_packs.py` 参数化家族生成。
- 全量模式要求每层非空，任一用例失败退出码为 1；`--heldout` 只在定时/手动 CI 中运行，避免进入日常调参回路。
- 与 pytest 的分工：pytest 覆盖单元与故障注入，evals 覆盖“模型轨迹 → 执行器 → 工具 → 下一轮上下文”。

当前计数：常规 243/243（smoke 21、tool 12、regression 201、safety 9）；held-out 全量 253/253。详见 `evals/README.md`。

## 5. 覆盖空白

| 模块 | 状态 |
|---|---|
| `floodmind/gateway/app.py`、`state.py`、`server.py` | 无测试 |
| `gateway/chainlit_app.py`、`chainlit_history.py` | 无生产引用、无测试 |
| `runtime/services/journal_backend.py` | 仅 Protocol，无测试 |
| `runtime/contracts/messages.py` | 无直接测试 |
| `runtime/adapters/*` | 仅导入烟测 |
| `skills/*/scripts/*` | 无测试 |
| `agent/context_runtime.py`、`agent_registry.py` | 仅弱断言覆盖 |
| `evals/` 本体 | 已有 `test_eval_case_packs.py` 覆盖结构与加载语义；执行器行为仍由 CI 直接执行 runner |

`tests/convergence/`、`tests/domain/`、`tests/fixtures/dsh/` 此前只剩 `__pycache__`，已随本轮清理删除；当前主工作区没有 `__pycache__` 目录残留（`.claude/worktrees/` 下的代理副本除外）。

## 6. 质量风险

| # | 风险 | 证据 | 影响 |
|---|---|---|---|
| 1 | 网关零自动化 | 无 gateway 测试 | SSE、权限 ASK、历史分页无回归；CI 不覆盖 |
| 2 | 平台耦合未加门禁 | `test_base_tools.py` 用 PowerShell `Start-Sleep` 并断言“超时” | 非 Windows 大概率失败；目前靠 skipif 保护部分用例 |
| 3 | 时序型 flaky | `sleep`/轮询/短超时，无 `pytest-timeout` | 高负载下超时 |
| 4 | 与私有实现强耦合 | 52 处直接访问 `_impl_*` 等内部符号 | 重构时大面积误报 |
| 5 | 弱断言 | `assert x >= 0`、多处 `is not None` | 行为未被完整验证 |
| 6 | Provider 只测 fake chunk | 真实网络/鉴权差异靠手工脚本 | 供应商回归不可门禁化 |
| 7 | 内部评测集仍以确定性轨迹为主 | 243 个常规用例 + 10 个 held-out，模型行为由脚本给出 | 不能替代真实模型质量评估；需持续换血 held-out |

`test_profile_system.py` 已不再是假测试：`check()` 失败会抛 `AssertionError`，pytest 可直接判红；手工 `__main__` 模式仍保留 PASS/FAIL 汇总。当前 9 个用例全部通过。

测试数字不再手写进 README 之外的运行说明；若文档与 CI 冲突，以 CI 输出为准。

## 7. 慢测试与平台依赖

慢测试主要集中在：

- `test_background_task_service.py`、`test_background_kill_chain.py`、`test_background_reconcile.py`
- `test_child_thread_cancellation.py`、`test_child_thread_quota.py`
- `test_sandbox_backend.py`、`test_sandbox_service.py`
- `test_mcp_client.py`（stdio、短超时）
- `test_scheduled_task_runtime.py`（真实 sleep）

平台：

| 平台 | 行为 |
|---|---|
| Windows | taskkill 进程树、junction 用例执行；Landlock 跳过 |
| Linux | Landlock 用例执行；junction/taskkill 跳过 |
| 无 symlink 权限 | 运行时 skip |

跳过慢测试的临时做法：

```bash
python -m pytest tests/ -q \
  --ignore=tests/test_background_task_service.py \
  --ignore=tests/test_child_thread_cancellation.py \
  --ignore=tests/test_child_thread_quota.py \
  --ignore=tests/test_sandbox_backend.py \
  --ignore=tests/test_mcp_client.py
```

## 8. 建议

1. 注册 marker（`slow`、`integration`、`windows_only`、`linux_only`），并加 `pytest-timeout`。
2. 为 gateway 补路由/SSE/权限 ASK 的契约测试，纳入现有 CI。
3. 为 `evals/runner.py` 增加一个最小 pytest 包装，避免只有 CI 才执行到评测逻辑。
