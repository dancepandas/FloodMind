# 15. 缺口闭合状态（G-01..G-22）

> 所属：Agent Harness 建设蓝图 / 第三部分 对照与采用
> 状态：2026-10-19 工作区审计（HEAD `b21f2e5` + 未提交的 harness 调整）
> 依据：当前代码、测试与 CI；逐项给出 `file:line` 与验证入口
> 结论：G-01..G-22 已闭合或已显式决策；第 4 节列出的路线图级增量（C1-C5、C7、D5）已落地，E 仍由 ADR 明确推迟

## 1. 审计口径

- 不以“文档写了”作为闭合依据；每项必须同时满足：运行时代码存在、契约被消费、有可执行验证。
- “已决策”指通过 ADR 明确不实现，并给出重新评估触发条件；它不等于“未完成”。
- 验证命令：

```bash
python -m pytest -q                              # 1565 passed, 1 skipped
python -m evals.runner                           # 243/243（smoke 21、tool 12、regression 201、safety 9）
python -m evals.runner --heldout                 # 253/253（含 10 个 held-out）
python scripts/calibrate_judge.py --judge rule --min-agreement 1.0   # 一致率 100%
```

## 2. 逐项状态

| 编号 | 状态 | 代码证据 | 验证证据 |
|---|---|---|---|
| G-01 Journal 写入 fencing | 已闭合 | `journal_writer.py:114,143-150,274,342` 在文件锁内校验 lease；`resume_service.py:52` 提供 `fence_check`，token 易主后旧 owner 写入被拒 | `tests/test_journal_fencing.py`；`tests/test_resume_service.py:48`；`tests/test_sdk_agent.py:806` |
| G-02 重试安全 | 已闭合 | `transport.py:15-16,42` 填充 `response_started`/`replay_safe`；`retry.py:23-48` 拒绝“已开始且不可重放”；`executor.py:818-848` 用本轮实际输出补齐判定，退避前检查 abort | `tests/test_retry_policy.py:181,241,284` |
| G-03 上下文窗口单一来源 | 已闭合 | `native_flood_agent.py:1351-1363` 统一委托 `resolve_context_window()`；`executor.py:1883-1898` 能力表缺失时回退执行器窗口并注入预算 | `tests/test_native_context_window.py` |
| G-04 压缩原子性 | 已闭合 | `context_compressor.py:267-276` 摘要为空时返回未压缩原文并保留事件；`executor.py:643` 不静默截断用户请求 | `tests/test_context_compressor.py:57`；`tests/test_journal_compact.py:200`；`tests/test_executor.py:828` |
| G-05 权限默认 fail-closed | 已闭合 | `permission_service.py:17-22` 固定优先级：策略 DENY > 全局 deny > ASK > allow；未分类写操作在无 AskService 时 DENY | `tests/test_permission_service.py:124,150,217`；`tests/test_permission_host_fixes.py` |
| G-06 凭证隔离 | 已闭合（最小形态） | `secret_store.py:1-11` 静态加密（DPAPI/Fernet）；`credential_env.py:1`、`process_sandbox.py:97`、`sandbox_backend.py:323` 子进程剔除凭证；`mcp_client.py:188` 不继承宿主环境 | `tests/test_secret_store.py`；`tests/test_credential_env.py` |
| G-07 CI 与故障注入 | 已闭合 | `.github/workflows/ci.yml:35-52` 运行单元/故障注入测试与评测门禁；`schedule` 每周定时回归，`workflow_dispatch` 可手动触发 | `tests/test_p3_acceptance.py:52,70`；`tests/test_journal_writer.py:79`；`tests/test_tool_execution_service.py:157` |
| G-08 工具输出上限与卸载 | 已闭合 | `tool_output_service.py:51-88` 超阈值卸载全文、返回头尾摘要与 `full_ref`；`tool_execution_service.py:548` 接线 | `tests/test_tool_output_service.py` |
| G-09 MemoryAdd 注册 | 已闭合 | `native_flood_agent.py:885-899` 将 `add_memory`（MemoryAdd）注册进 orchestrator；专家注册表按策略排除 | `tests/test_sdk_agent.py:789-801`；`tests/test_base_tools.py:192,216` |
| G-10 Skill 写后回读 | 已闭合 | `native_flood_agent.py:1875-1877` UpdateSkill 与 CreateSkill 共用 `_write_skill_md_verified`，失败回滚 | `tests/test_skill_md_writeback.py` |
| G-11 两份 mcp.json | 已闭合 | `settings.py:660` 单一契约校验；`settings.py:718-742` 对项目级 `.floodmind/mcp.json` 显式告警并指出正确路径；不再静默兜底 | `tests/test_mcp_config_validation.py` |
| G-12 上下文回放 | 已闭合 | `executor.py:772-773,1940-1986` 每次模型调用前落 `context.projection.committed` Manifest（含预算、来源、哈希） | `tests/test_projection_manifest.py` |
| G-13 trace 指标 | 已闭合 | `tracing_service.py` 聚合指标与 `breakdown`；`otlp_exporter.py` 导出 OTLP/HTTP JSON；`:300-318` 使用 `gen_ai.*` 语义；`metrics.jsonl` 落指标快照 | `tests/test_trace_metrics.py`；`tests/test_usage_accounting.py`；`tests/test_otlp_exporter.py`；`tests/test_tracing_otlp.py` |
| G-14 内部评测集与门禁 | 已闭合 | `evals/runner.py` 四层（smoke/tool/regression/safety）+ held-out；脚本化轨迹驱动真实执行器；CI 运行并上传报告 | `python -m evals.runner` 243/243；`--heldout` 253/253；`.github/workflows/ci.yml:43-52` |
| G-15 记忆失效与遗忘 | 已闭合 | `semantic_store.py:53-137` `valid_to`/`expire_due()`；`services.py:46,107` 启动与 checkpoint 维护；`base_tools.py:1385` MemoryAdd `ttl_days`；`recall.py` 新鲜度衰减 | `tests/test_memory_expiry.py` |
| G-16 子代理输入输出契约 | 已闭合 | `native_flood_agent.py:2220,2668` 结构化任务包（目标/技能/工作区/边界/返回格式），父上下文不注入原始用户文本 | `tests/test_subagent_contract.py` |
| G-17 工具评测与轨迹指标 | 已闭合 | `evals/real_tools.py` 真实工具接缝；runner 的 tool 层记录 `tool_calls`/`invalid_calls`/`tool_accuracy` | `evals/runner.py` tool 4/4 |
| G-18 token 预算 | 已闭合 | `types.py:119-121`、`api.py:136`、`executor.py:664-672` 单 run 累计预算，超限显式终态且不再发起调用 | `tests/test_token_budget.py`；evals `regression-token-budget` |
| G-19 路由与回退 | 已闭合 | `model_gateway.py` 回退链（未输出前切换、每 run 复位、事件可观测）；`child_thread_runtime.py:302` 子代理模型路由与配额叠加；`native_flood_agent.py:1408` 装配 | `tests/test_model_routing.py` |
| G-20 渐进披露 | 已闭合 | `tool_loading.py:17-42` 支持 eager/catalog/progressive，默认 progressive；`GetTool`/`SearchTools` 按需加载，核心工具白名单 | `tests/test_tool_loading.py` |
| G-21 A2A / AG-UI | 已决策（不实现） | `docs/adr/ADR-008-protocol-adoption.md`：MCP 仅工具，不预留抽象；给出重新评估触发条件 | ADR 评审；无协议占位代码 |
| G-22 多代理收益基线 | 已闭合 | `benchmarks/multi_agent/` 固定 4 任务、single/delegated 对照、收益判定与逐任务退化统计；`ADR-007` 固化“先测量后拆分” | `tests/test_multi_agent_benchmark.py`；离线运行不调用模型 |

## 3. 状态说明

- G-06 采用的是“加密存储 + 子进程环境过滤”的最小形态，没有引入独立凭证代理服务。这符合路线图对部署成本的取舍；若未来需要跨进程凭证签发，再按 ADR 评估。
- G-07 的 CI 在 push / pull request 上运行，并增加每周 `schedule` 与 `workflow_dispatch`；恢复、超时、fencing、并发等故障注入用例随同一套 pytest 定期回归。
- G-13 已有指标聚合、`breakdown` 下钻与 OTLP/HTTP exporter；未配置 `observability.otlp_endpoint` 时只落本地 `trace.jsonl` / `metrics.jsonl`。
- G-21 是显式不实现，不是待办；任何协议实现都必须先更新 ADR-008。
- G-22 只建立测量机制，没有在本次审计中执行 `--live`，因此不存在“多代理更优”的结论。

## 4. 路线图级增量（不属于 G-01..G-22）

以下项目来自 `14-adoption-roadmap.md` 的阶段目标，当前状态如下：

| 路线图项 | 当前状态 | 验证证据 |
|---|---|---|
| C1 评测集规模 | 常规门禁 243 例（smoke 21、tool 12、regression 201、safety 9），另有 10 例 held-out；JSON 手工用例 + `evals/case_packs.py` 参数化家族 | `python -m evals.runner` 243/243；`--heldout` 253/253；`tests/test_eval_case_packs.py` |
| C2 判定器 | 状态/工作区断言仍是门禁；新增 `RuleJudge` / `ModelJudge` 与 20 条人工标注校准集，LLM 评审未校准前不得作为发布门禁 | `scripts/calibrate_judge.py --judge rule` 一致率 100%；`tests/test_judge_calibration.py` |
| C3 指标下钻 | `aggregate_usage` 输出 totals/by_run/by_model/by_step/by_tool；`metrics.jsonl` 每次 flush 带 `breakdown`；span 携带 `harness.run_id` / `step_key` | `tests/test_usage_accounting.py`；`tests/test_trace_metrics.py` |
| C4 OTel 对齐 | `otlp_exporter.py` 将已完成 span 转为 OTLP/HTTP JSON（`gen_ai.*` / `harness.*` 属性）；`TracingService.from_settings()` 按 `observability` 配置导出，失败不阻断本地 flush | `tests/test_otlp_exporter.py`；`tests/test_tracing_otlp.py` |
| C5 故障注入 | CI 增加每周定时与手动触发；`--heldout` 只在 schedule/manual 运行 | `.github/workflows/ci.yml:8-10,50-52` |
| C6 发布门禁 | 冒烟 + 工具 + 回归 + 安全四层全量运行，缺层或失败即退出码 1 | `evals/runner.py`；CI eval gate |
| C7 成本归因 | `model_pricing` 配置进入 `Settings`；span/事件带 run/step 归因；`scripts/metrics_report.py` 输出 token 与金额 | `tests/test_metrics_report.py`；`tests/test_usage_accounting.py` |
| D5 规划产物 | `PlanStore` 原子写 `plan.json` / `plan.md`；`create_plan`/`update_plan` 与 run 收尾（含 auto-advance）落盘；新 run 恢复未完成计划并注入上下文 | `tests/test_plan_store.py`；`tests/test_plan_persistence.py` |
| E 扩展 | 多代理与协议均由 ADR 明确推迟 | 无 |

这些增量不构成 `13-floodmind-gap-analysis.md` 的结构性缺口；E 阶段仍须先满足 ADR-007 / ADR-008 的重新评估条件。

## 5. 结论

`13` 中列出的 22 项结构性缺口已全部闭合或已显式决策。当前代码的验证基线为：

- 单元与契约测试：1565 passed, 1 skipped；
- 内部评测集：常规 243/243（四层），held-out 253/253；
- 判定器校准：确定性基线一致率 100%；
- CI：单元/故障注入 + 判定器校准 + 四层评测门禁，并每周定时回归；
- 协议与多代理：ADR-007 / ADR-008 明确边界，无占位实现。

后续工作应集中在真实模型质量评估、held-out 换血与观测后端运营，而不是继续增加运行时组件。
