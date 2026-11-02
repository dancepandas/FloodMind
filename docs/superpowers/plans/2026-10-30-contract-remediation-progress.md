# 契约整改进度

本轮无提交、无推送；原有开发契约交付保留。对应计划：2026-10-30-contract-remediation.md。

## 工具结果（进行中）

- 新增 tests/test_tool_result_contract.py：真实 ApplyPatch 转 ToolSpec 后通过 ToolExecutionService 执行；部分成功后缺失文件更新失败；Pydantic validator 内部异常；成功正文包含错误词不触发重试。
- RED：三个行为均按预期失败（completed/completed/错误重试提示）。最初 AgentTool 未转 ToolSpec 的组装错误已纠正，不算行为 RED。
- GREEN：三项通过；相关五模块 58 passed（随后补充部分副作用 metadata 回归再次 RED→GREEN，三项通过）。
- 决策：common.tool_output.ToolOutput 使用带显式状态的文本承载结果，保留现有文本消费接口；生产者显式聚合分段状态，执行服务和重试保护共享判定，不添加 Update failed 子串补丁。风险：中间层 str() 会丢失语义，需检查消费者及回归。
- 部分失败保留每段状态和实际成功文件，不宣称回滚；do_not_retry_same_call 标记失败，需继续检查消费者。
- 执行器：新增真实 ToolExecutionService + 临时 Journal 回归，成功正文含“错误”仍误触发连续失败终止，RED→仅消费 status→GREEN；最初未绑定 Journal 的组装错误不计行为 RED。
- 权限拒绝：三个已有 ApplyPatch 权限用例增加语义断言，全部 RED→显式 ToolOutput error→GREEN；六模块 95 passed，diff check 通过。
- Profile：模块 autouse fixture 隔离 home、profile、真实 settings 模块缓存和 cwd；两个局部 home override 使用 monkeypatch.context 恢复；直接入口改为 pytest，避免绕过隔离。两个入口各 9 passed。此项为测试基础设施修改，未运行危险旧版本获取 RED，尚缺调用方配置恢复的外层回归。
- 实例隔离：交错 A/B/A 运行回归先观察到调用方 sentinel（RED），移除构造期 memory 绑定、在 worker 中绑定本实例后 GREEN。
- 恢复：SDK 恢复 memory 绑定先 RED；新增 SDK/CLI 共用 resume_execution，绑定 Journal memory、PathService 与 RuntimeContext，在复制 Context 中执行并清理活跃 authority、释放 lease。SDK 保留历史回放句柄。正常 stream 装配尚未合并，不能宣称运行作用域整改完成。
- CLI 旧回归失败实因为 SimpleNamespace 缺少 _stream_lock，不增加生产 fallback；测试改用真实 Agent 和模拟模型流。SDK/resume/Profile 组合 28 passed；新增恢复异常回归直接通过（已有清理行为的覆盖，不计 RED→GREEN），SDK 模块 8 passed。
- 决策：权限声明审查发现 MemoryAdd 的只读 callback 并未绕过 state_write 硬门，AddTaskExperience 的 path 实为经验树逻辑路径而非文件路径；后续移除误导声明时须保持 state_write 的统一策略，不把逻辑路径接入文件写权限。
- MemoryAdd：补无 RuntimeContext 时 ToolSpec 权限检查回归，原只读 callback 直接 ALLOW（RED）；移除该 callback 后由 state_write 策略 fail closed（GREEN）。这是直接 ToolSpec 调用路径的缺陷，执行服务路径已有统一权限硬门。四模块 33 passed，diff check 通过。
- 全量首次结果：2 failed、1648 passed、2 skipped；两项失败均为 eval case pack 仍要求 completed 正文触发连续失败。迁移 F21/F22 为显式 error，并保留失败终止及成功清零预期；评测模块 21 passed；随后完整 pytest 为 1651 passed、2 skipped（85.44s）。未以恢复正文扫描来迁就旧评测。
- Profile 补充：新增 tests/test_profile_isolation.py，在临时 caller sentinel 环境执行真实 Profile 测试及 fixture 退出，验证 env、settings 根目录和缓存对象、cwd 完整恢复且 caller 目录无写入。该覆盖直接通过，不计 RED→GREEN；组合 10 passed。删除直接入口迁移后不可达的吞异常 runner、递归全量 runner 和失实注释；两个入口仍各 9 passed。
- 正常/恢复共享装配：原恢复 RunContext 漏传 max_total_tokens，真实 checkpoint 预算回归 RED（调用模型并返回 over budget）→抽取 native `_assemble_run_context`→GREEN。恢复编排迁至 `agent/native/resume_execution.py`，SDK/CLI 同用，不保留旧路径 shim。
- 恢复终态：预算失败公开事件原为 started/completed，真实回归 RED→按 canonical journal replay 终态发 started/failed→GREEN；不读取可能已被 executor 深拷贝的旧 loop state。
- 部分恢复装配异常：在真实 `_set_session_context` 边界注入异常，验证 Native/executor authority、current context 清理与后继 lease 可获取；直接通过，属于已有行为覆盖。
- AddTaskExperience：逻辑经验路径不应当作文件路径；无 RuntimeContext 直接 ToolSpec 检查原误 ALLOW（RED），移除错误文件写 callback、保留 state_write policy 后 DENY（GREEN）。
- 最新针对性验证：tool result、SDK identity、resume service、token budget、SDK agent 共 117 passed（15.04s），git diff --check 通过。此前全量 1652 passed、2 skipped 与 held-out 253/253 均早于上述修改，不代表最新树结果。
- contracts 依赖：显式注入 PermissionService 的 ToolSpec 回归先因不支持参数 RED→移除 contracts 对 tools.session_context 的反向 import、执行服务在边界传入服务→GREEN；三模块 45 passed。无显式服务时仍 DENY，不新增第二 ContextVar 状态源。
- 实例隔离补充：真实 Agent 分别写入私有 Journal 历史，再在 A/B/A 模型边界调用真实 MemorySearch，确认只命中本实例历史；直接通过，属于已有行为覆盖。初版搜索尚未完成的当前用户消息失败源于历史格式化有意排除待回复消息，已改为搜索先前完成轮，不计行为 RED。
- 独立审查再次因 fable 服务 503 终止，无审查结论，不宣称通过。
- 全量验证发现评测 real-tool 装配仍用缺少 permission_service 的 SimpleNamespace：4 failed、1653 passed、2 skipped；held-out 222/253。迁移 evals.runner 为真实 RuntimeContext，不给生产添加 getattr/fallback；组合 38 passed，最新完整 pytest 1657 passed、2 skipped（87.01s），held-out 253/253。首次日志打印因 Windows GBK 无法编码替换字符失败，后续打印使用 backslashreplace；pytest 实际失败已按日志如实处理。
- 会话装配：在真实 base_tools.set_session_context 边界注入异常，恢复原吞异常继续运行（RED：未抛异常）；移除 native 包装层的吞异常行为后 fail closed，验证模型未调用、活跃绑定清空且 lease/lock 可重新获取（GREEN）。SDK/resume 109 passed，最新全量 1658 passed、2 skipped（88.39s），最新 held-out 253/253，diff check 通过。
- 清理异常：真实 memory.bind_journal 解绑异常使 resume 跳过 current context 恢复（RED），将恢复放入 finally 后 GREEN；正常 stream 同类异常使 worker 退出而公开流永远等待（RED），改为逐项清理并报告异常，继续释放 lease、恢复 context 和结束流。首轮全量暴露清理错误事件过早发布导致公开流先于资源释放结束（1 failed、1659 passed、2 skipped）；延后错误事件到清理及锁释放之后，最新全量 1660 passed、2 skipped（87.74s），held-out 253/253，diff check 通过。Ruling：清理失败必须对宿主可见，但不得阻断其余独立资源释放；不将异常吞为成功。
- ApplyPatch 单段部分失败：update 已写入、随后 move 抛异常时，原聚合只看成功段，错误报告 partial_side_effects=False（真实执行服务回归 RED）。生产者在写入后的移动边界返回显式部分副作用，聚合保留段级标记，并在正文说明未回滚及须核实状态（GREEN）。初版测试未模拟移动审批，权限门阻止写入，不计行为 RED；最终测试显式模拟已批准权限。针对性 11 passed；最新完整 pytest 1661 passed、2 skipped（118.46s），held-out 253/253，diff check 通过。
- MemorySearch 工具描述/参数 schema 曾宣称搜索 Skills，而实现只搜索当前 Agent 对话历史；schema 回归 RED→纠正描述→GREEN。ToolOutput 模块说明同步显式状态优先及 str 转换丢语义的边界。该树完整 pytest 1662 passed、2 skipped（120.74s），held-out 253/253。
- 幂等边界补充：失败且有部分副作用的 Journal 事件不会被 find_committed_result 当作成功重放；新增覆盖直接通过，不计 RED→GREEN；结果/幂等/执行事件组合 23 passed。Ruling：do_not_retry_same_call 是执行结果中的核验提示，不在本轮新增跨调用永久阻断策略；失败不是成功提交，不能伪装为 completed 重放。若需自动阻断同参部分失败，须另定义解锁/核验协议，当前不宣称已提供。
- 最终独立审查：fable 再次 HTTP 503，无结论；改用可用模型独立审查，确认 stream close 提前释放 worker 锁、resume 缺审批通道、resume 清理前公开成功终态三项；按下述回归修复，不把服务失败记为审查通过。
- Stream 生命周期：真实阻塞模型回归先 RED（消费者 close 后可获取运行锁），增加线程启动归属判断，仅在线程未启动时由消费者兜底释放；worker 清理后释放锁，后继运行成功（GREEN），SDK 模块当时 15 passed。
- Resume 终态：扩展真实 memory.bind_journal 解绑异常回归，公开状态原为 started/completed（RED）；终态发布移至独立资源清理之后，异常路径发 failed，仍恢复 context、释放 lease/lock（GREEN）。Ruling：公开 resume 终态描述本次宿主调用整体结果，清理失败必须为 failed；Journal 中模型执行已完成的事实不回写成未执行 — 代价是宿主必须区分执行事实与调用生命周期，不能依据 API 异常盲目重试副作用。
- Resume 审批：真实 AskService + Journal + SDK respond_permission 回归先 RED（无 emitter，自动拒绝）；将 session emitter 绑定/清理收敛到正常与恢复共享装配/解绑，SDK 恢复临时订阅 EventBus，复用事件收集和 on_event 分发，finally 移除订阅（GREEN）。回归同时验证运行后 emitter/订阅不残留；SDK/resume/权限组合 35 passed。
- 最终全量首轮 1 failed、1664 passed、2 skipped：旧 __new__ 测试装配缺少 _current_run_context。补齐测试装配，不在生产增加兼容 fallback；最新完整 pytest 1665 passed、2 skipped（121.64s），离线评测含 held-out 253/253（smoke 21、tool 13、regression 209、safety 10），git diff --check 通过。
- 验收边界：本轮计划范围已完成；未提交、未推送、未合并或发布，保留全部工作树交付。以上是 WIP 树验证，不代表远端 CI 或干净提交版发布验收。此前文档示例和分发检查退出码均为 0；不把它们当作最终提交版验收。部分副作用自动跨调用阻断仍未提供，沿用上述核验提示边界。
