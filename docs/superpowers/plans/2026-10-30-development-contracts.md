# FloodMind 开发契约与自动验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 交付第一批可核实的开发规范，并将工具示例、交付产物和 CI 汇总检查接入自动验证。

**Architecture:** 保留现有原生 SDK、pytest 与 evals。规范引用现有实现和测试；只新增开发验证脚本，不修改运行时行为。其他子系统规范作为后续独立工作，不在本批创建占位文件。

**Tech Stack:** Python >=3.10、pytest、stdlib、setuptools/wheel、开发构建工具 build、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-10-30-development-contracts-design.md`

## Global Constraints

- Python >=3.10。
- 不修改业务运行时契约以迎合文档。
- 不增加产品运行依赖；构建工具仅用于开发/CI。
- 不提交、不推送、不改远端保护，除非用户另行明确要求。
- 不触碰业务文档、归档、docs/key.txt 或 .floodmind/mcp.json。
- 所有用户可见文档使用中文说明及必要技术标识，不使用 emoji。

## Review Focus

- 文档误把模型 schema 等同运行时验证：Task 1 用错误参数验证实际拒绝路径，并明确校验入口。
- 同步工具超时后仍写入：Task 1 引用现有超时不确定性测试，不宣称强杀或安全重试。
- 文档标记拼错导致零示例假通过：Task 2 检查必需集合、重复及未知标识。
- 安装测试误导入源码：Task 3 用源码树外 CWD、隔离模式和安装位置断言。
- 汇总任务在上游 skipped 后假绿：Task 4 参数化 success/failure/cancelled/skipped/空集合和非法输入。

---

### Task 1: 开发契约样板与真实工具示例

**Files:**
- Create: `CONTRIBUTING.md`, `.github/pull_request_template.md`
- Create: `docs/development/README.md`, `docs/development/architecture-boundaries.md`, `docs/development/adding-a-tool.md`
- Create: `docs/testing/README.md`, `docs/testing/change-matrix.md`, `docs/testing/fixtures-and-isolation.md`, `docs/testing/artifacts-and-release.md`
- Modify: `docs/README.md`, `docs/DEVELOPER_GUIDE.md`, `tests/README.md`
- Test: `tests/test_development_examples.py`

**Interfaces:**
- Consumes: `floodmind.tools.agent_tool.AgentTool`、`floodmind.agent.runtime.contracts.tools.ToolSpec/ToolCall/ToolResult`、`ToolExecutionService.execute(..., journal_authority=...)`。
- Produces: 工具文档中标记为 `python contract-example=tool-basic` 的一个独立可执行示例；示例定义 `make_tool()` 返回真实 ToolSpec，普通业务函数无外部副作用。

- [x] 阅读完整 `floodmind/tools/agent_tool.py`、工具执行服务相关执行/校验/异常路径、实际注册表、权限契约；记录当前实现依据。不得借用不存在的 SDK 模块路径。
- [x] 新增 `test_documented_tool_runs_through_execution_service`、`test_documented_tool_rejects_invalid_input`、`test_documented_tool_reports_execution_error`。加载文档唯一标记块，构造实际 ToolSpec 和真实执行服务；注册表用最小 get 适配器，不 Mock 规格或执行服务。成功断言 status、name、tool_call_id 和内容；失败断言业务未执行或异常归一为 error。
- [x] 执行 `python -m pytest tests/test_development_examples.py -q`，确认缺失文档/示例时失败。
- [x] 编写工具契约和真实示例，按已核实的 JSON Schema parameters 前置校验和 Pydantic args_schema/validate_input 行为表达校验，不夸大任意 schema 对象的严格验证保证。确认只读标记、权限默认值、结果文本归一、超时线程残留与副作用不确定性。引用现有 `tests/test_tool_execution_service.py`、`tests/test_tool_output_status.py` 等对应测试。
- [x] 编写其余首批规范：权威层级、模块依赖方向、核心准入、变更矩阵、测试隔离和产物验收。PR 清单要求列明变更类型、规范影响、实际命令与结果、跳过原因，不以打勾替代测试。
- [x] 同步现有索引与指南，删除会漂移的当前测试数量；保留明确标注的历史记录。测试 `python -m pytest tests/test_development_examples.py tests/test_tool_execution_service.py tests/test_tool_output_status.py -q` 全绿。

### Task 2: 文档示例显式登记和反漂移验证

**Files:**
- Create: `scripts/check_doc_examples.py`, `tests/test_doc_examples.py`
- Modify: `tests/test_development_examples.py`, `scripts/README.md`, `docs/testing/README.md`

**Interfaces:**
- Consumes: Task 1 的工具示例。
- Produces: `extract_examples(text: str) -> dict[str, str]`；`load_examples(root: Path) -> dict[str, str]`；CLI `python scripts/check_doc_examples.py`。清单常量将 `tool-basic` 映射到工具文档路径。提取器不执行文本，CLI 对登记的代码编译并在临时 CWD 中执行。

- [x] 新增参数化测试：正常块可提取；缺失必需标记、重复、未知标识、未闭合块、语法错误必须失败；普通未登记 Python 围栏不执行。仓库任意其他文件不隐式加入执行范围。
- [x] 执行 `python -m pytest tests/test_doc_examples.py -q` 确认失败。
- [x] 用 stdlib 实现解析、固定登记和 CLI；错误输出给出文件与标记。将 Task 1 测试的临时解析逻辑换成 `load_examples`，不复制同一解析器。
- [x] 执行 `python scripts/check_doc_examples.py` 及 `python -m pytest tests/test_doc_examples.py tests/test_development_examples.py -q` 全绿。
- [x] 文档明确：检查登记示例及其组装测试，不声称校验全部文档语义。修改签名必须更新消费者、示例和契约测试。

### Task 3: sdist 到 wheel 的干净安装验证

**Files:**
- Create: `scripts/check_distribution.py`, `tests/test_distribution_checks.py`
- Modify: `docs/testing/artifacts-and-release.md`, `scripts/README.md`

**Interfaces:**
- Consumes: pyproject 包数据配置和 CLI `floodmind --help`。
- Produces: `verify_installed_package(python: Path, cwd: Path) -> None`；CLI `python scripts/check_distribution.py`，失败返回非零。

- [x] 写纯逻辑/子进程边界测试：验证参数顺序、检查使用源码外 CWD 与 `-I`、缺失模板/Skill 资源时失败、临时目录即使失败也清理、非零子命令不得吞掉。Mock 构建和安装的耗时进程边界，不以这些测试声称真实安装通过。
- [x] 执行 `python -m pytest tests/test_distribution_checks.py -q` 确认失败。
- [x] 实现临时源副本（排除 .git、构建缓存、egg-info、虚拟环境及用户未跟踪文件；仅用 `git ls-files -z` 获取受控输入，空清单报错）。用 stdlib subprocess 调用开发依赖 build 生成 sdist，安全解包本次自建 sdist 后构建 wheel；创建临时 venv，安装 wheel。不得修改现有 dist/build/egg-info。
- [x] 安装探针以 `-I` 在临时目录导入公共 Agent/ModelClient/ToolSpec，断言模块路径在该环境安装目录内；通过 importlib.resources 检查 settings_template.json 和一个从打包源码确认的内置 SKILL.md；比对发行元数据和 __version__；运行隔离模块入口或临时环境 CLI --help（先核实真实入口）。
- [x] 执行测试并实际运行 `python scripts/check_distribution.py`；如需联网安装依赖，使用已授权代理路径，不把代理写入产品配置。记录实际失败或成功，不用 mock 代替本步骤。

### Task 4: 稳定的 CI 汇总检查

**Files:**
- Create: `scripts/check_ci_results.py`, `tests/test_ci_results.py`
- Modify: `.github/workflows/ci.yml`, `.github/workflows/README.md`, `CONTRIBUTING.md`

**Interfaces:**
- Consumes: 环境变量 NEEDS，格式为 GitHub `toJSON(needs)`。
- Produces: `validate_results(needs: object) -> list[str]` 返回失败原因；CLI 无错误返回 0，否则 1。允许的唯一成功结果为字符串 success，空或非对象数据报错。

- [x] 新增参数化测试：全部 success 通过；failure/cancelled/skipped/未知/缺失 result、空对象、非法 JSON 失败；CLI 正负退出码可验证。
- [x] 执行 `python -m pytest tests/test_ci_results.py -q` 确认失败，再实现验证脚本，确保全绿。
- [x] 保留现有 Linux 3.10/3.12 测试；增加 Windows 3.12 include 矩阵，报告 artifact 名包含 OS 避免冲突。增加独立 contracts 任务运行文档测试、检查脚本语法及实际产物验证，开发环境安装 build。保留 held-out 调度条件。
- [x] 增加 `required` 作业，`name: CI required`、`if: always()`、`needs: [test, contracts]`，checkout 后向验证脚本传递 NEEDS。取消和跳过不得误判成功。
- [x] 校验 YAML 能加载、矩阵及依赖关系符合预期，执行 `python -m pytest tests/test_ci_results.py -q`。说明远端 CI 运行和 ruleset 配置仍需后续实际观察与授权，不宣称已启用。

### Task 5: 综合验收与交接

**Files:**
- Modify: 本计划任务状态、各入口文档中实际命令和适用范围。

**Interfaces:**
- Consumes: Tasks 1–4 的规范、测试与命令。
- Produces: 实际验证报告和未完成事项清单，不自动提交或发布。

- [x] 执行 `python scripts/check_doc_examples.py`、新增测试、`python scripts/check_distribution.py`。
- [x] 执行 `python -m pytest -q`、`python scripts/calibrate_judge.py --judge rule --min-agreement 1.0`、`python -m evals.runner --json <临时路径>`，记录真实结果，不将历史数量当作本轮结果。
- [x] 检查 `git diff --check`、文档相对链接、敏感信息与工作树边界；确认未触碰业务文件/归档/用户配置。
- [x] 独立审查首批规范与源码是否相符、产物探针是否可误导入源码、汇总检查是否假绿；修正后重跑受影响测试。审查不运行不必要的多代理编排。
- [x] 汇报：本地已验证内容、尚未运行的 Linux/Windows 远端矩阵、未配置的远端保护、后续子系统规范范围。不提交、不推送，等待明确授权。

## 后续独立计划

内核/事件、状态/恢复、插件生命周期、Provider/Skill/MCP 各自先完成源码和消费者梳理，再按本批模板编写专项规范及测试。远端保护在具备认证、确认目标分支和明确授权后操作；不隐含在本计划中。
