# FloodMind 开发契约与验证闭环设计

日期：2026-10-30
状态：方案已获用户认可；具体实施计划待审阅。

## 目标

建立“开发契约 → 对应测试 → CI 验证 → 合并约束”的闭环，避免开发依赖隐含约定和补丁。以现有原生 SDK 为基础，不引入 DSH、A2A 或平行运行时。

## 范围与顺序

1. 首个可验收交付：规范入口和权威层级、架构边界、新增与修改工具契约、测试策略与变更矩阵、贡献流程与 PR 清单。同步修正现有指南入口及历史数量描述；关键工具示例通过真实执行服务验证。
2. 自动验证：文档示例、wheel/sdist 构建与隔离安装验证、稳定 CI 汇总检查。保留既有 pytest、judge 校准、确定性评测、held-out 调度。
3. 后续独立交付：内核与事件、状态与恢复、插件、Provider、Skill/MCP 的专项契约。每个子系统先核对源码和调用方，再写规范并绑定回归测试，不用空白占位文档制造完成感。

远端 GitHub/GitLab 合并保护需要另行核实、授权和操作。工作流提供所需检查，不声称远端保护已经启用。

## 文档治理

`docs/development/README.md` 定义规范入口、适用范围和冲突处理。开发规范约束未来变更；当前行为以已核实的实现和契约测试为依据。规范与源码冲突时必须记录差距并修正，不能以历史计划覆盖实际行为。ADR 记录决策原因，指南负责入门，知识图谱负责结构事实，历史计划不构成现行契约。

每份契约包含：适用范围、最小正确示例、框架保证、作者责任、生命周期与失败语义、禁止事项、影响范围、验收测试。示例必须使用本项目真实接口。不能将 DSH 的 JSON 返回、冻结参数或取消信号等机制写成 FloodMind 已有保证。

首批规范位置：
- `CONTRIBUTING.md`
- `docs/development/README.md`
- `docs/development/architecture-boundaries.md`
- `docs/development/adding-a-tool.md`
- `docs/testing/README.md`
- `docs/testing/change-matrix.md`
- `docs/testing/fixtures-and-isolation.md`
- `docs/testing/artifacts-and-release.md`
- `.github/pull_request_template.md`

## 工具契约依据

核对 `ToolSpec`、`AgentTool`、注册表、`ToolExecutionService`、权限及输出归一服务。`ToolResult.status` 现行为 completed/error/awaiting_permission；`ToolSpec.parameters` 是模型可见 schema，不能未经验证就宣称它自动完成所有运行时参数校验。明确 args_schema、validate_input、权限和执行顺序。超时不等于线程已经停止，写入结果不确定时禁止承诺安全重试。

工具示例不访问模型或用户配置；测试通过真实执行服务及真实规格对象，检查结果身份、状态、验证拒绝和异常分支。不得用 Mock 工具或手工构造 ToolResult 代替该示例的组装验证。

## 测试策略

分为单元、契约、组装、产物四层。复用 tests 和 evals，不新建平行评测体系。以变更类型规定最低证据：工具、插件、执行器、事件、持久化、Provider、打包、文档分别需要对应的正常与失败路径。回归测试需能在重新引入缺陷时失败。

默认无密钥、无真实模型调用，外部不确定性在模型/网络边界替换。临时目录承载状态，测试清理线程、进程及端口；平台跳过必须有原因。真实模型评测独立运行，不代替确定性门禁。不设任意全仓覆盖率阈值，不一次性引入全仓类型或格式迁移。

## 自动化边界

文档使用显式命名的 Python 围栏标记登记可执行示例。检查器只执行受版本控制、明确登记的示例，不执行所有文档围栏或下载内容。示例缺失、重复、未知标识及语法错误必须失败。说明哪些内容受检查，不夸大为全文语义一致性检查。

产物验证在临时目录构建 sdist，再从 sdist 构建 wheel，使用临时虚拟环境安装并在源码树外验证公共导入、版本、CLI --help、settings 模板及实际打包的 Skill 资源。隔离 Python 路径，避免 pythonpath/source checkout 掩盖缺文件。仅 core 安装时不得断言需要 gateway extra 的功能。不得删除用户文件、全局环境或仓库已有构建目录。

CI 保留 Linux Python 3.10/3.12 测试，增加 Windows Python 3.12 测试及独立验证任务；若出现现存平台失败，应明确报告并处理，不静默标绿。汇总检查名为 `CI required`，仅在所有阻塞依赖 success 时成功；failure/cancelled/skipped/未知结果均失败。常规 PR 不需要外部密钥。静态检查先采用示例语法与新增检查脚本的编译验证，不以大范围 lint 整改扩展本次工作。

## 约束

- Python >=3.10。
- 不修改业务运行时契约以迎合文档。
- 不增加产品运行依赖；构建工具仅用于开发/CI。
- 不提交、不推送、不改远端保护，除非用户另行明确要求。
- 不触碰业务文档、归档、docs/key.txt 或 .floodmind/mcp.json。
- 所有用户可见文档使用中文说明及必要技术标识，不使用 emoji。

## 验收

首批规范与现有源码逐条核对；工具示例和文档登记机制有正负测试；产物验证来自 sdist 重建并安装的 wheel；汇总门禁有失败/跳过测试；本地全量 pytest、judge、确定性 eval 执行并如实报告。远端 CI、平台矩阵及保护状态只有实际观察后才标记为已验证。
