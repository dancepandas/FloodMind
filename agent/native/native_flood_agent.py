"""
Native Agent Runtime - NativeFloodAgent

自研 Native Agent，替代 LangChain FloodAgent。
保持与现有 web_server.py SSE 事件协议兼容。
"""

import contextvars
import json
import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.native.types import (
    AgentLoopState,
    AgentResult,
    ExecutionPlan,
    RunContext,
)
from agent.runtime.contracts.tools import ToolSpec
from agent.native.artifact_watcher import ArtifactWatcher
from agent.native.event_bus import EventBus, StepEventBus
from agent.native.executor import NativeAgentExecutor
from agent.native.message_builder import MessageBuilder
from agent.native.model_client import ModelClient
from agent.native.planner import Planner
from agent.native.tool_runtime import native_from_agent_tool

from config.settings import settings

logger = logging.getLogger(__name__)

_active_input_var: contextvars.ContextVar[str] = contextvars.ContextVar("active_user_input", default="")


class _InstanceToolRegistry:
    """Per-agent instance tool registry (not global)."""

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def tools_schema(self) -> List[dict]:
        return [t.to_openai_tool() for t in self._tools.values()]

    def register_tools(self, tools: list) -> None:
        for tool in tools:
            spec = native_from_agent_tool(tool)
            self.register(spec)


class NativeFloodAgent:
    SYSTEM_PROMPT = """你是大水云科技开发的FloodMind。

## 当前系统时间
{current_time_context}

{project_context}

## 角色职责
你负责五类事情：
1. 分析用户意图和最终目标
2. 规划任务步骤
3. 把步骤分发给执行单元
4. 汇总执行结果并回答用户
5. 直接完成轻量无副作用任务（见下方"轻量执行边界"）

## 角色边界
### 轻量执行边界（主 Agent 可直接完成）
以下类型的任务你可以直接完成，无需分发给执行单元：
- 意图澄清、任务拆解、计划生成
- 小规模文本整理、摘要、格式化（数据量 ≤ 10 条）
- 简单参数校验、路径校验、结果一致性检查
- 只读查询类工具调用（knowledge_search、search_artifacts、read_artifact、get_skill、search_memory）
- 不产生持久副作用的轻量计算
- 对执行单元结果的补充解释、质量检查、重试决策
- 简单问答、问候、知识解释

### 必须委派边界（主 Agent 严禁直接执行）
以下类型的任务必须分发给执行单元，不得亲自执行：
- 写文件、生成 Word/Excel/PDF
- 跑模型、跑脚本、批量数据处理
- 调用外部系统或产生业务副作用
- 构造复杂 JSON / 大数据表（数据量 > 10 条）
- 多步骤领域任务（预报、调度、报告生成等）
- 任何需要权限确认、可破坏、不可逆的操作
- run_script、exec_python_file、exec_bash、write_text_file 等执行类工具

### 通用原则
- 如问好之类的简单问题直接回答就行
- 不要把任务复杂化
- 不要过度思考

## 可用工具
- `create_plan`：【delegated / requires_plan 时必须调用】创建结构化执行计划，明确用户意图、预期交付物和执行步骤。lightweight 任务无需调用此工具
- `delegate_execution_specialist`：串行委派单个任务给执行单元
- `delegate_parallel`：并行委派多个互不依赖的任务给执行单元，可显著缩短总执行时间
- `get_skill`：查看 skill 的详细说明、脚本、参数和规则
- `search_artifacts`：搜索当前会话或历史可复用产物
- `read_artifact`：读取文本类产物
- `knowledge_search`：检索知识库（知识查询时，优先使用）
- `web_search`：检索网络资料（knowledge_search搜索结果不够支撑回答时，搜索网络资料补充）
- `search_memory`：检索历史对话和技能文档
- `update_project_instructions`：将用户偏好或规则写入 AGENTS.md，使其在后续所有对话中生效
- `create_scheduled_task`：创建后台定时任务，用户要求未来、每天、定时、自动执行任务时使用
- `list_scheduled_tasks`：查询当前会话的定时任务
- `cancel_scheduled_task`：取消或停用定时任务
- `search_task_experience`：检索历史任务执行经验（遇到类似任务时，先搜索经验避免重复踩坑）
- `add_task_experience`：手动添加任务执行经验到经验树

## 定时任务处理
当用户表达"每天、明天、某个时间、定时、自动、后台执行、提前安排任务"等需求时：
1. 你必须先调用 `create_plan`，再调用 `create_scheduled_task` 写入任务列表，不要立即执行业务任务。
2. `command` 只保留未来真正要执行的业务任务，必须去掉"每天/定时/几点执行"等调度表达，避免后台执行时再次创建定时任务。
3. 每日重复任务使用 `repeat="daily"` 和 `run_time="HH:MM"`；一次性任务使用 `repeat="none"` 和 `scheduled_at`。
4. 任务默认绑定当前会话，用户后续可从前端查看该任务生成的新增文件。
5. 用户询问已有定时任务时调用 `list_scheduled_tasks`；用户取消定时任务时调用 `cancel_scheduled_task`。

## 用户偏好处理
当用户表达长期偏好、规则或习惯时（如"以后都用PNG格式"、"不要生成PDF"）：
1. 先确认用户意图：此偏好仅本次对话生效，还是所有对话都生效？
2. 仅本次对话 → 调用 `add_memory` 写入会话记忆
3. 所有对话 → 进一步确认作用域：
   - 仅本项目 → `update_project_instructions(scope="project")`
   - 全局所有项目 → `update_project_instructions(scope="global")`
4. **写入前必须向用户展示将要写入的内容，等待用户确认后再执行 `update_project_instructions`**
5. 写入后告知用户：此偏好已持久化，将在后续所有对话中自动生效

## 执行工具细节
- 调用工具时一次只传一个参数：例如要查看两个skill时，应该是`get_skill（skill1）`，等待返回结果，再进行`get_skill（skill2）`，等待返回结果
- excel的sheet命名字符最长允许31个字符，所以stationCode太长时，sheet_name可能会被截断

## 可用执行单元
- `delegate_execution_specialist`：执行单步落地任务，包括数据提取、转换、结构化文件生成、Excel 导出、模型相关脚本执行；如已明确 skill，委派时一并传 `skill_name`
- `delegate_parallel`：并行委派多个互不依赖的任务。当计划中有多个步骤可同时执行时使用

## 并行委派规则
当计划中有多个步骤之间无依赖关系时，使用 `delegate_parallel` 一次性并行委派：
- 各任务必须互不依赖（不读写同一文件、不依赖彼此的输出产物）
- 每个任务仍遵循"内容先落地"规则（文档类任务先写中间文件再委派）
- 有依赖关系的步骤仍用 `delegate_execution_specialist` 串行委派
- 不要对需要用户确认权限的任务使用并行委派
- 典型场景：先生成内容文件和图表（并行），再合并为最终文档（串行，依赖前两步产物）

## 敖江流域子任务编码
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~水动力模型区间断面预报`、`水动力模型区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`
- 详细信息查看 aojiang-hydro SKILL.md文档

## 可用 skills
{skill_catalog}

## 调度工作流
### 0. 任务分级判断（强制）
在处理用户请求时，你必须先判断任务级别：
- **lightweight**：轻量无副作用任务（简单问答、只读查询、小规模文本整理 ≤10 条、参数校验等）→ 直接完成，无需 create_plan，无需委派
- **delegated**：单步重任务（写文件、跑脚本、生成 Excel/PDF、批量数据等）→ 调用 create_plan 后委派执行单元
- **requires_plan**：多步骤业务流程（预报+导出、分析+绘图+报告等）→ 调用 create_plan 后按步骤委派

判断规则：
- 涉及 run_script / exec_python_file / exec_bash / write_text_file → 至少是 delegated
- 涉及文件产物（Excel/Word/PDF/PNG等）→ 至少是 delegated
- 数据量 > 10 条 → 至少是 delegated
- 多步骤领域任务 → requires_plan
- 只读查询、简单问答、小文本整理 → lightweight

### 1. 创建执行计划（delegated / requires_plan 时强制）
在分发任何执行任务之前，你必须先调用 `create_plan` 工具：
- user_goal: 用户的原始意图（不要包含上下文注入信息）
- deliverables: 预期最终交付物类型，逗号分隔（image/excel/report/other）
- steps: 执行步骤JSON数组，每个元素含 title、executor、skill_name(可选)、purpose、expected_deliverables

### 2. 分析目标
先明确：
1. 用户最终要什么交付物
2. 当前输入属于原始数据、中间结果还是最终结果
3. 当前缺的是哪一个阶段
4. 当前任务是基于之前的任务成果继续还是开启新的任务
5. 若是基于之前的任务成果，绝对不能重跑之前的任务，必须严格按照已有成果开展工作

### 3. 优先确认 skill
如果任务明显对应某个业务 skill 或导出能力，必须先调用 `get_skill` 查看详细说明，再决定下一步。

### 4. 规划并分发
每次分发给 `delegate_execution_specialist` 的任务必须满足：
1. 只有一个核心动作
2. 明确输入文件或输入产物
3. 明确预期输出
4. 不要把用户原始长文本整包塞给执行单元
5. 如果你已经确定要使用某个 skill，直接明确要求执行

### 4.5 文档生成的内容传递规则（强制）
当任务涉及生成 Word/PDF/报告等文档时，执行单元无法访问你的对话历史和搜索结果，因此：
1. **你必须先将文档的完整内容写入一个中间文件**（如 `report_content.md`），包含所有章节的完整正文、数据、分析结论
2. 然后委派执行单元时，task 中明确指定该内容文件路径，例如："根据 report_content.md 的内容，使用 docx skill 生成 Word 文档 report.docx"
3. **严禁只传大纲或标题**，中间文件必须包含每个章节的完整正文内容
4. 这条规则优先级高于"不要把超长文本塞给执行单元"——文档内容必须完整落地到文件

### 5. 结合校验继续推进
只有当本轮任务明确承诺了文件产物时，才在流程结束后执行代码级最终文件存在性检查。
如果最终文件检查明确指出缺失文件，优先按缺失文件结果继续分发，不要自己重新写成模糊任务。

### 6. 整理最终回答
最终只向用户总结：
1. 已完成什么
2. 生成了哪些最终文件
3. 如果未完成，还缺什么

## 调度原则
1. 默认负责规划、分发、汇总；同时允许直接完成轻量无副作用任务
2. 如果有相关 skill，先查 skill，再决定是否委派
3. 对执行单元只传当前这一步的执行指令，不传用户原始长输入和多余会话背景；但文档生成类任务必须先将完整内容写入中间文件再委派（见"4.5 文档生成的内容传递规则"）
4. 严禁把超长 JSON 直接塞进任务描述
5. 必须严格遵循 SKILL.md 及相关文档
6. 不要过度解读任务，调度执行单元要谨慎！
7. 凡涉及持久化产物、脚本执行、外部调用、批量数据、领域计算或权限风险的任务，必须分发给执行单元

## 产物意图判定
在决定是否生成面向用户的持久化文件时，请根据用户意图自行判断：
- 用户明确要求"生成、导出、保存、下载、报告、Excel、Word、PDF、图片"等文件时 → 生成文件作为最终交付物
- 用户只要求"计算、分析、查询、告诉我结果、看看数据"等 → 最终交付物默认为文字答案，不需要额外生成文件
- 模型或工具运行过程中必须产生的中间文件（如 input.json、result.json、result.xlsx 等），属于内部过程产物，可在回答中附带提及供用户下载，但不得表述为用户要求的最终交付物
- 不要主动为用户未要求的文件类型生成报告或导出文件
- 如果用户只要求文字结果，即使工具天然输出文件，也以文字汇总为主，文件仅作为可下载附件

## 输出规范
- 最终输出路径使用相对路径即可
- 最终输出不要包含会话环境内部信息
- 标准 Markdown 格式输出
"""

    EXECUTION_SPECIALIST_PROMPT = """你是 Execution Specialist 执行单元。

{project_context}

## 你的职责
你只负责：
1. 严格执行调度 agent 已明确分配的单步任务
2. 严格根据调度任务运行已有 skill 脚本
3. 编写并执行临时 Python 脚本完成任务

## 执行原则
- 把输入任务视为已定稿的执行指令，不要重写目标，不要重新拆解流程，不要补充下游计划
- 只围绕当前这一步行动；做完立即返回，不扩展上下游
- 如果指令缺文件、缺参数、缺前置产物，就明确指出缺什么，不要自己猜业务意图
- 只需根据任务命令执行即可，非必要不查看skill的具体信息

## 执行工具细节
- 调用工具时一次只传一个参数：例如要查看两个skill时，应该是`get_skill（skill1）`，等待返回结果，再进行`get_skill（skill2）`，等待返回结果

## 强约束
- 不要重新理解用户需求；按当前任务执行
- 不要猜测或杜撰 skill 中未声明的脚本、参数或字段
- 不要把超长 JSON 直接塞进工具参数
- 不要根据聊天文本手工搬运大数组；优先从原始文件读取
- 不要继续规划下游步骤；你只完成当前委派任务
- 如果任务目标已经达成，不要重复调用工具

## 可使用工具
1. `get_skill`
2. `search_artifacts`
3. `read_artifact`
4. `run_script`
5. `write_text_file`
6. `exec_python_file`
7. `search_tool_error_memory`
8. `web_search`（当需要从网络检索资料补充信息时使用）

## 可使用skills
{skill_catalog}

## 输出要求
- 简洁说明本次任务是否完成
- 明确返回直接结果，如生成文件路径、读取/搜索结果、关键输出摘要
- 不要给出下一步建议，不要说明后续如何使用，由调度 agent 决定后续动作
"""

    _ARTIFACT_EXTENSIONS = {".json", ".csv", ".xlsx", ".xls", ".docx", ".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg"}

    def __init__(
        self,
        llm_service=None,
        memory=None,
        session_id: str = "",
        enable_search: bool = False,
        enable_reasoning: bool = False,
        **kwargs,
    ):
        self.llm_service = llm_service
        self.memory = memory
        self.session_id = session_id
        self._enable_search = enable_search
        self._enable_reasoning = enable_reasoning

        self._orchestrator_registry = _InstanceToolRegistry()
        self._specialist_registry = _InstanceToolRegistry()
        self._event_bus = EventBus()
        self._message_builder = MessageBuilder()
        self._planner = Planner(event_bus=self._event_bus)

        self._model_client: Optional[ModelClient] = None
        self._orchestrator_executor: Optional[NativeAgentExecutor] = None
        self._specialist_executor: Optional[NativeAgentExecutor] = None
        self._tool_executor: Optional[Any] = None
        self._artifact_watcher: Optional[ArtifactWatcher] = None
        self._artifact_lock = threading.Lock()

        self._skill_catalog = ""
        self._active_user_message = ""
        self._step_start_time = 0.0
        self._last_loop_state: Optional[AgentLoopState] = None
        self._current_run_context: Optional[RunContext] = None
        self._orchestrator_extra_body: dict = {}

        self._cached_experience_context: str = ""
        self._cached_experience_version: int = -1

        self._init_tools()
        self._init_model_client()
        self._init_executors()
        if settings.agent.enable_chronos_warmup:
            self._warmup_chronos()

        logger.info("NativeFloodAgent 初始化成功")

    _chronos_warmup_done = False
    _chronos_warmup_lock = threading.Lock()

    @staticmethod
    def _warmup_chronos():
        with NativeFloodAgent._chronos_warmup_lock:
            if NativeFloodAgent._chronos_warmup_done:
                logger.info("Chronos-2 预热已完成，跳过重复预热")
                return
            NativeFloodAgent._chronos_warmup_done = True

        def _warmup():
            try:
                from skills.chronos_pipeline import get_pipeline
                get_pipeline()
            except Exception as e:
                logger.warning(f"Chronos-2 预热失败（不影响功能）: {e}")
                with NativeFloodAgent._chronos_warmup_lock:
                    NativeFloodAgent._chronos_warmup_done = False
        t = threading.Thread(target=_warmup, daemon=True, name="chronos-warmup-native")
        t.start()

    def _init_tools(self) -> None:
        from tools import (
            get_skill, run_script, exec_bash, exec_python_file, write_text_file,
            search_tool_error_memory, search_artifacts, check_artifact_exists, read_artifact, knowledge_search,
            web_search, add_memory, search_memory, update_project_instructions,
            create_scheduled_task, list_scheduled_tasks, cancel_scheduled_task,
            set_rag_config, set_memory_instance, reset_retry_guard,
        )
        from tools.agent_tool import ToolRegistry as _GlobalToolRegistry
        from memory.task_experience import get_task_experience_capture
        from agent.runtime.contracts.permissions import PermissionBehavior, PermissionRule, ToolPermissionPolicy
        from agent.runtime.services.permission_service import PermissionService, set_permission_service
        from agent.runtime.services.ask_service import get_ask_service, set_ask_service
        from agent.runtime.services.path_service import PathService, set_path_service
        from agent.runtime.services.tool_execution_service import ToolExecutionService
        from skills import SKILL_REGISTRY
        from config.settings import settings as _settings

        if self.memory is not None:
            set_memory_instance(self.memory)
            if self.memory._llm is None:
                self.memory.set_llm(self.llm_service)

        set_rag_config(
            enabled=_settings.rag.enabled,
            persist_dir=_settings.rag.persist_dir,
            embedding_model=_settings.rag.embedding_model,
            top_k=_settings.rag.top_k,
        )

        path_service = PathService()
        set_path_service(path_service)

        ask_service = get_ask_service()
        set_ask_service(ask_service)

        perm_svc = PermissionService.create_default(ask_service=ask_service, path_service=path_service)
        perm_svc.add_allow_rule(PermissionRule(
            name="allow_session_output",
            pattern=r"data[\\/\\\\]+sessions",
            behavior=PermissionBehavior.ALLOW,
            reason="会话输出目录默认允许写入",
        ))
        set_permission_service(perm_svc)

        from tools.agent_tool import set_permission_manager
        set_permission_manager(perm_svc)

        base_tools = [
            get_skill, search_artifacts, read_artifact, knowledge_search,
            search_memory, update_project_instructions,
            create_scheduled_task, list_scheduled_tasks, cancel_scheduled_task,
        ]
        # 从全局 ToolRegistry 获取任务经验工具
        for _tname in ("search_task_experience", "add_task_experience"):
            _t = _GlobalToolRegistry.get(_tname)
            if _t:
                base_tools.append(_t)
        if self._enable_search:
            base_tools.append(web_search)
        execution_tools = [
            get_skill, run_script, exec_python_file, write_text_file,
            search_tool_error_memory, search_artifacts, read_artifact,
        ]
        if self._enable_search:
            execution_tools.append(web_search)

        self._skill_catalog = "\n".join(
            f"- {s.name}: {s.description}"
            + (f" (v{s.version})" if s.version and s.version != "1.0" else "")
            + (f" [provides: {', '.join(s.provides_tools)}]" if s.provides_tools else "")
            for s in SKILL_REGISTRY
        ) + "\n- get_skill: 按需获取任意技能的完整参数说明"

        self._orchestrator_registry.register_tools(base_tools)
        self._specialist_registry.register_tools(execution_tools)

        self._orchestrator_registry.register(ToolSpec(
            name="create_plan",
            description="【delegated / requires_plan 时必须调用】在分发任何执行任务之前，先调用此工具创建结构化执行计划。明确用户意图、预期交付物和执行步骤。lightweight 任务（简单问答、只读查询等）无需调用此工具。",
            parameters={
                "type": "object",
                "properties": {
                    "user_goal": {"type": "string", "description": "用户的原始意图描述"},
                    "deliverables": {"type": "string", "description": "预期最终交付物类型，逗号分隔。可选: image, excel, report, other"},
                    "steps": {
                        "type": "array",
                        "description": "执行步骤JSON数组，每个元素含title、executor、skill_name(可选)、purpose、expected_deliverables(JSON数组)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "executor": {"type": "string"},
                                "skill_name": {"type": "string"},
                                "purpose": {"type": "string"},
                                "expected_deliverables": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                    },
                },
                "required": ["user_goal", "deliverables", "steps"],
            },
            func=self._handle_create_plan,
            is_readonly=True,
            is_destructive=False,
            is_concurrency_safe=True,
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        ))

        self._orchestrator_registry.register(ToolSpec(
            name="delegate_execution_specialist",
            description="当你已经完成任务拆分，且需要执行单元无脑执行某一个明确步骤时调用。适用于数据提取、文件转换、中间 JSON/CSV、Excel 导出、运行 skill 脚本、编写最小临时 Python 脚本等单步落地任务。若已明确要复用某个 skill，必须同时传入 skill_name。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "交给执行单元的明确任务说明，应尽量具体、短小、可执行"},
                    "skill_name": {"type": "string", "description": "若当前任务明确要求复用某个 skill，则传入对应的 skill 名称"},
                },
                "required": ["task"],
            },
            func=self._handle_delegate_specialist,
            is_readonly=False,
            is_destructive=True,
            is_concurrency_safe=False,
            permission_policy=ToolPermissionPolicy(policy_type="internal", reason="编排层内部委派，实际权限由 specialist 内部工具逐次检查"),
        ))

        self._orchestrator_registry.register(ToolSpec(
            name="delegate_parallel",
            description="并行委派多个互不依赖的任务给执行单元。当计划中有多个步骤之间无依赖关系时使用，可显著缩短总执行时间。各任务必须互不依赖（不读写同一文件、不依赖彼此的输出）。有依赖关系的步骤仍用 delegate_execution_specialist 串行委派。",
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "可并行执行的任务列表，每个元素含 task(任务说明)、skill_name(可选)、step_key(可选，对应 create_plan 中的步骤ID)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "交给执行单元的明确任务说明"},
                                "skill_name": {"type": "string", "description": "若任务明确要求复用某个 skill，传入 skill 名称"},
                                "step_key": {"type": "string", "description": "对应执行计划中的步骤ID，如 step-1"},
                            },
                            "required": ["task"],
                        },
                    },
                    "max_concurrent": {"type": "integer", "description": "最大并发数，默认3，最大5", "default": 3},
                },
                "required": ["tasks"],
            },
            func=self._handle_delegate_parallel,
            is_readonly=False,
            is_destructive=True,
            is_concurrency_safe=True,
            permission_policy=ToolPermissionPolicy(policy_type="internal", reason="编排层内部并行委派，各 specialist 独立运行"),
        ))

        self._tool_executor = ToolExecutionService(
            permission_service=perm_svc,
            path_service=path_service,
            ask_service=ask_service,
            set_session_context_fn=self._set_session_context,
        )

    def _init_model_client(self) -> None:
        if self.llm_service is not None:
            api_key = self.llm_service.api_key
            base_url = self.llm_service.base_url
            model_name = self.llm_service.model_name
            temperature = self.llm_service.temperature
            max_tokens = self.llm_service.max_tokens
            self._model_client = ModelClient(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if self.llm_service.enable_reasoning:
                self._orchestrator_extra_body = {"enable_thinking": True}
            return

        from config.model_presets import get_preset, resolve_api_key, resolve_base_url, get_default_model_key

        model_key = get_default_model_key()
        preset = get_preset(model_key)
        if not preset:
            raise ValueError(f"未知的模型预设: {model_key}")

        api_key = resolve_api_key(preset)
        base_url = resolve_base_url(preset)

        if self._enable_reasoning and preset.get("supports_reasoning"):
            temperature = preset.get("thinking_temperature", 0.2)
            max_tokens = preset.get("thinking_max_tokens", 4096)
            self._orchestrator_extra_body = {"enable_thinking": True}
        else:
            temperature = preset.get("default_temperature", 0.3)
            max_tokens = preset.get("default_max_tokens", 4096)
            self._orchestrator_extra_body = {}

        self._model_client = ModelClient(
            api_key=api_key,
            base_url=base_url,
            model_name=preset["model_name"],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _init_executors(self) -> None:
        from agent.context_runtime import ContextRuntime

        self._context_runtime = ContextRuntime(
            context_window=settings.agent.context_window,
        )
        self._context_runtime.prefetch()

        project_context = self._context_runtime.load_project_rules()
        current_time_context = ContextRuntime.load_current_time_static()

        orchestrator_prompt = self.SYSTEM_PROMPT.format(
            skill_catalog=self._skill_catalog,
            current_time_context=current_time_context,
            project_context=project_context,
        )

        specialist_prompt = self.EXECUTION_SPECIALIST_PROMPT.format(
            skill_catalog=self._skill_catalog,
            project_context=project_context,
        )

        self._orchestrator_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=50,
            extra_body=self._orchestrator_extra_body,
            system_prompt=orchestrator_prompt,
            tools_schema=self._orchestrator_registry.tools_schema(),
            tool_registry=self._orchestrator_registry,
            require_plan_before_delegate=True,
        )

        self._specialist_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=50,
            system_prompt=specialist_prompt,
            tools_schema=self._specialist_registry.tools_schema(),
            tool_registry=self._specialist_registry,
        )

    def _handle_create_plan(self, user_goal: str = "", deliverables: str = "", steps: Any = "") -> str:
        steps_str = json.dumps(steps, ensure_ascii=False) if isinstance(steps, (list, dict)) else str(steps)
        try:
            parsed_steps = json.loads(steps_str) if steps_str else []
        except json.JSONDecodeError:
            parsed_steps = [{"title": steps_str[:60] if steps_str else "执行任务", "executor": "execution_specialist", "purpose": user_goal, "expected_deliverables": []}]

        if not isinstance(parsed_steps, list):
            parsed_steps = [parsed_steps]

        normalized_steps = []
        for i, raw_step in enumerate(parsed_steps):
            if not isinstance(raw_step, dict):
                raw_step = {"title": str(raw_step)[:60]}
            step_id = raw_step.get("step_id") or f"step-{i + 1}"
            expected = raw_step.get("expected_deliverables", [])
            if isinstance(expected, str):
                try:
                    expected = json.loads(expected)
                except Exception:
                    expected = [{"type": expected}]
            if not isinstance(expected, list):
                expected = [expected] if expected else []
            normalized_steps.append({
                "step_id": step_id,
                "title": str(raw_step.get("title", "") or f"步骤 {i + 1}"),
                "executor": str(raw_step.get("executor", "") or "execution_specialist"),
                "skill_name": str(raw_step.get("skill_name", "") or ""),
                "purpose": str(raw_step.get("purpose", "") or ""),
                "status": "pending",
                "expected_deliverables": expected,
                "output_artifacts": [],
                "output_summary": "",
                "error_message": "",
                "attempt_count": 0,
            })

        deliverable_types = [d.strip() for d in deliverables.split(",") if d.strip()] if deliverables else []
        goal_deliverables = [{"type": dt} for dt in deliverable_types]

        plan = ExecutionPlan(
            plan_id=f"plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            user_message=user_goal,
            goal_deliverables=goal_deliverables,
            steps=normalized_steps,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        if self._last_loop_state is not None:
            self._last_loop_state.plan = plan
            self._last_loop_state.user_message = user_goal

        self._event_bus.emit_workflow_plan(
            title=user_goal,
            steps=[
                {
                    "key": s["step_id"],
                    "label": s["title"],
                    "title": s["title"],
                    "status": s["status"],
                    "detail": s["purpose"],
                    "expected_deliverables": s["expected_deliverables"],
                }
                for s in normalized_steps
            ],
        )

        summary = f"执行计划已创建: {len(normalized_steps)} 个步骤, 交付物: {deliverables or '无特定类型'}"
        logger.info("[create_plan] %s", summary)
        return summary

    def _handle_delegate_specialist(self, task: str = "", skill_name: str = "") -> str:
        task = (task or "").strip()
        skill_name = (skill_name or "").strip()
        if not task:
            return "错误：委派给 execution_specialist 的 task 不能为空"

        specialist_input = self._build_specialist_user_input(task, skill_name)

        context = self._current_run_context
        if context is None:
            context = RunContext(
                session_id=self.session_id,
                user_text=specialist_input,
                attachments=[],
                output_dir=self._get_output_dir(),
                upload_dir=self._get_upload_dir(),
            )

        step_key = f"delegate-{int(time.time_ns())}"
        if self._last_loop_state is not None and self._last_loop_state.plan is not None:
            pending_step = self._last_loop_state.plan.next_pending_step()
            if pending_step:
                step_key = pending_step.get("step_id", step_key)
                pending_step["status"] = "running"
        self._event_bus.emit_workflow_step(
            step_key=step_key,
            status="running",
            title=task[:60] if task else "委派执行",
            detail=skill_name or "",
        )

        self._step_start_time = time.time()
        with self._artifact_lock:
            if self._artifact_watcher:
                self._artifact_watcher.take_snapshot()

        result = self._specialist_executor.run(
            context=context,
            user_text=specialist_input,
            attachments=context.attachments,
            memory_messages=[],
            abort_check=context.abort_check,
        )

        output = result.final_output or ""

        artifacts = []
        with self._artifact_lock:
            if self._artifact_watcher:
                new_artifacts = self._artifact_watcher.detect_new_artifacts()
                artifacts = [a.file_path for a in new_artifacts]

        has_tool_success = any(tr.status == "completed" for tr in result.tool_results) if result.tool_results else False
        has_artifacts = bool(artifacts)
        step_status = "completed" if (result.final_output or has_tool_success or has_artifacts) else "error"
        if self._last_loop_state is not None and self._last_loop_state.plan is not None:
            plan_step = self._last_loop_state.plan.find_step(step_key)
            if plan_step:
                plan_step["status"] = step_status
        self._event_bus.emit_workflow_step(
            step_key=step_key,
            status=step_status,
            title=task[:60] if task else "委派执行",
            outcome=output[:200] if output else "",
        )

        payload = {
            "stage": "execution_specialist",
            "stage_label": "Execution Specialist",
            "result_type": "intermediate",
            "status": "completed",
            "user_goal": _active_input_var.get(),
            "task": task,
            "skill_name": skill_name,
            "summary": output,
            "artifacts": artifacts,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _handle_delegate_parallel(self, tasks: Any = "", max_concurrent: int = 3) -> str:
        """并行委派多个独立任务给执行单元"""
        if isinstance(tasks, str):
            try:
                tasks = json.loads(tasks)
            except json.JSONDecodeError:
                return "错误：tasks 参数必须是 JSON 数组"

        if not isinstance(tasks, list) or not tasks:
            return "错误：tasks 必须是非空数组"

        max_concurrent = max(1, min(max_concurrent or 3, 5))

        context = self._current_run_context
        if context is None:
            context = RunContext(
                session_id=self.session_id,
                user_text="[并行委派]",
                attachments=[],
                output_dir=self._get_output_dir(),
                upload_dir=self._get_upload_dir(),
            )

        specialist_prompt = self._specialist_executor.system_prompt if self._specialist_executor else ""

        results: Dict[str, Dict[str, Any]] = {}

        def _run_single(idx: int, task_def: Dict[str, Any]) -> tuple:
            task_text = (task_def.get("task") or "").strip()
            skill_name = (task_def.get("skill_name") or "").strip()
            step_key = task_def.get("step_key") or f"parallel-{idx}"

            if not task_text:
                return (step_key, {"status": "error", "summary": "task 为空", "artifacts": []})

            self._event_bus.emit_workflow_step(
                step_key=step_key,
                status="running",
                title=task_text[:60],
                detail=skill_name or "",
            )

            step_event_bus = StepEventBus(self._event_bus, step_key)

            specialist_executor = NativeAgentExecutor(
                model_client=self._model_client,
                tool_executor=self._tool_executor,
                event_bus=step_event_bus,
                message_builder=MessageBuilder(),
                max_iterations=50,
                system_prompt=specialist_prompt,
                tools_schema=self._specialist_registry.tools_schema(),
                tool_registry=self._specialist_registry,
            )

            specialist_input = self._build_specialist_user_input(task_text, skill_name)

            with self._artifact_lock:
                if self._artifact_watcher:
                    self._artifact_watcher.take_snapshot()

            try:
                result = specialist_executor.run(
                    context=context,
                    user_text=specialist_input,
                    attachments=context.attachments,
                    memory_messages=[],
                    abort_check=context.abort_check,
                )
            except Exception as e:
                logger.error("[并行委派] 任务 %s 执行异常: %s", step_key, e)
                self._event_bus.emit_workflow_step(step_key=step_key, status="error", title=task_text[:60], outcome=str(e)[:200])
                return (step_key, {"status": "error", "summary": str(e)[:200], "artifacts": []})

            output = result.final_output or ""

            artifacts = []
            with self._artifact_lock:
                if self._artifact_watcher:
                    new_artifacts = self._artifact_watcher.detect_new_artifacts()
                    artifacts = [a.file_path for a in new_artifacts]

            has_tool_success = any(tr.status == "completed" for tr in result.tool_results) if result.tool_results else False
            step_status = "completed" if (output or has_tool_success or artifacts) else "error"

            if self._last_loop_state is not None and self._last_loop_state.plan is not None:
                plan_step = self._last_loop_state.plan.find_step(step_key)
                if plan_step:
                    plan_step["status"] = step_status

            self._event_bus.emit_workflow_step(
                step_key=step_key,
                status=step_status,
                title=task_text[:60],
                outcome=output[:200] if output else "",
            )

            return (step_key, {
                "status": step_status,
                "summary": output[:500] if output else "",
                "artifacts": artifacts,
            })

        with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            futures = {}
            for i, task_def in enumerate(tasks):
                future = pool.submit(_run_single, i, task_def)
                futures[future] = i

            for future in as_completed(futures):
                try:
                    step_key, result_data = future.result()
                    results[step_key] = result_data
                except Exception as e:
                    idx = futures[future]
                    step_key = f"parallel-{idx}"
                    results[step_key] = {"status": "error", "summary": str(e)[:200], "artifacts": []}
                    logger.error("[并行委派] future 异常: %s", e)

        payload = {
            "stage": "parallel_delegation",
            "stage_label": "Parallel Execution",
            "result_type": "intermediate",
            "status": "completed" if all(r.get("status") != "error" for r in results.values()) else "partial",
            "user_goal": _active_input_var.get(),
            "tasks_count": len(tasks),
            "results": results,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _validate_artifacts(self, agent_result: AgentResult) -> None:
        """最终产物校验：检查模型声称生成的文件是否真实存在（迁移文档§9.4, §4.8）。"""
        if not agent_result or not agent_result.final_output:
            return
        output_dir = self._get_output_dir()
        if not output_dir or not os.path.isdir(output_dir):
            return

        mentioned_files = set()
        for ext in sorted(self._ARTIFACT_EXTENSIONS, key=len, reverse=True):
            pattern = re.compile(r'(?<=[/\\])[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-.]*' + re.escape(ext) + r'(?![\w\u4e00-\u9fff\-.])' + r'|(?<![/\w\u4e00-\u9fff\\-])[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-.]*' + re.escape(ext) + r'(?![\w\u4e00-\u9fff\-.])', re.IGNORECASE)
            for match in pattern.findall(agent_result.final_output):
                if len(match) < 5 or len(match) > 120:
                    continue
                mentioned_files.add(match)

        missing = []
        for fname in mentioned_files:
            if not os.path.isfile(os.path.join(output_dir, fname)):
                missing.append(fname)

        if missing:
            warning = f"⚠️ 以下文件在回答中被提及但未在输出目录中找到: {', '.join(missing[:5])}"
            logger.warning("[产物校验] %s", warning)
            agent_result.final_output += f"\n\n{warning}"
            self._event_bus.emit({"type": "artifact_warning", "content": warning})

    @staticmethod
    def _build_specialist_user_input(task: str, skill_name: str = "") -> str:
        normalized_task = (task or "").strip()
        normalized_skill_name = (skill_name or "").strip()
        if not normalized_task:
            return ""

        lines = [
            "你现在只执行一个已经明确分配的子任务。",
            "把它当作执行指令，不要重写目标，不要补充计划，不要重新理解用户需求。",
            "",
            "[核心任务]",
            normalized_task,
        ]

        if normalized_skill_name:
            lines.extend([
                "",
                "[指定skill]",
                normalized_skill_name,
                "",
                "如果当前任务明确要求复用该 skill，优先使用run_script执行skill中的脚本，遇到参数错误时再调用 `get_skill` 查看其脚本与参数，再执行。",
            ])

        return "\n".join(lines).strip()

    @staticmethod
    def _set_session_context(session_id: str, output_dir: str) -> None:
        try:
            from tools.base_tools import set_session_context
            set_session_context(session_id, output_dir)
        except Exception as e:
            logger.warning("Failed to set session context: %s", e)

    def _on_permission_ask(self, tool_name: str, tool_input: Dict[str, Any], reason: str) -> bool:
        from agent.runtime.services.ask_service import get_ask_service
        call_id = str(tool_input.get("__call_id", "")) if isinstance(tool_input, dict) else ""
        clean_input = {k: v for k, v in tool_input.items() if k != "__call_id"} if isinstance(tool_input, dict) else tool_input
        bridge = get_ask_service()
        from agent.runtime.contracts.permissions import PermissionAskRequest
        return bridge.request(PermissionAskRequest(
            session_id=self.session_id,
            call_id=call_id,
            tool_name=tool_name,
            reason=reason,
            tool_input=clean_input,
        ))

    def _build_experience_context(self) -> str:
        """注入经验摘要到上下文（渐进压缩，只给摘要而非全部叶子），带版本号缓存"""
        if not settings.task_experience.enabled:
            return ""
        try:
            from memory.task_experience import get_task_experience_store
            store = get_task_experience_store()
            if not store.has_experiences():
                return ""
            current_version = store.get_version()
            if current_version == self._cached_experience_version and self._cached_experience_context:
                return self._cached_experience_context
            md = store.build_summary_context()
            if not md:
                return ""
            self._cached_experience_context = f"[历史任务执行经验摘要]\n{md}\n\n需要查看具体经验详情时，请使用 browse_experience_tree 或 drill_down_experience 工具。"
            self._cached_experience_version = current_version
            return self._cached_experience_context
        except Exception as e:
            logger.warning("构建经验上下文失败: %s", e)
            return ""

    def _get_output_dir(self) -> str:
        if self.session_id:
            data_dir = os.environ.get('DATA_DIR', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')))
            output_dir = os.path.join(data_dir, "sessions", self.session_id, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            return output_dir
        try:
            from tools.base_tools import get_current_session_output_dir
            d = get_current_session_output_dir()
            if d:
                return d
        except Exception:
            pass
        return os.path.abspath(os.path.join("data", "agent_state"))

    def _get_upload_dir(self) -> str:
        if self.session_id:
            data_dir = os.environ.get('DATA_DIR', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')))
            upload_dir = os.path.join(data_dir, "sessions", self.session_id, "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            return upload_dir
        return ""

    def stream(self, user_input: str, enable_reasoning: bool = False, user_message: str = "", abort_check: Optional[Any] = None):
        """
        流式运行智能体，通过 Queue + threading 实现真实流式。
        与 FloodAgent.stream() 输出格式兼容。
        """
        try:
            logger.info("NativeFloodAgent 收到用户输入(流式): %s...", user_input[:50])
            _active_input_var.set(user_message or user_input)
            self._active_user_message = user_message or user_input

            if hasattr(self.memory, "set_status_callback"):
                self.memory.set_status_callback(None)

            active_notice = None
            if hasattr(self.memory, "add_user_message"):
                result = self.memory.add_user_message(user_input)
                if result:
                    active_notice = result
                    yield {"type": "system", "content": f"【{result}】"}

            q: queue.Queue = queue.Queue()
            result_holder: Dict[str, Any] = {}

            def _run_loop() -> None:
                try:
                    logger.info("[RUN_LOOP] === _run_loop started, session=%s ===", self.session_id)
                    output_dir = self._get_output_dir()
                    upload_dir = self._get_upload_dir()
                    self._set_session_context(self.session_id, output_dir)

                    self._artifact_watcher = ArtifactWatcher(output_dir=output_dir, upload_dir=upload_dir)
                    self._artifact_watcher.take_snapshot()

                    context = RunContext(
                        session_id=self.session_id,
                        user_text=user_input,
                        attachments=[],
                        output_dir=output_dir,
                        upload_dir=upload_dir,
                        enable_reasoning=enable_reasoning,
                        abort_check=abort_check,
                    )
                    self._current_run_context = context

                    self._last_loop_state = AgentLoopState(
                        run_id=f"run-{int(time.time())}",
                        user_message=user_input,
                        original_input=user_input,
                    )

                    if enable_reasoning:
                        self._orchestrator_executor.extra_body = {"enable_thinking": True}
                    else:
                        self._orchestrator_executor.extra_body = {}

                    self._event_bus.set_queue(q)

                    from agent.runtime.services.ask_service import get_ask_service
                    ask_service = get_ask_service()
                    ask_service.set_emit_fn(lambda event: self._event_bus.emit(event), session_id=self.session_id)

                    memory_messages = []
                    if hasattr(self.memory, "get_full_messages"):
                        memory_messages = self._message_builder.build_memory_messages(
                            self.memory.get_full_messages()
                        )
                    elif hasattr(self.memory, "get_messages"):
                        memory_messages = self._message_builder.build_memory_messages(
                            self.memory.get_messages()
                        )

                    # 注入对话历史到系统提示词（传入上下文信息用于压缩判断）
                    if hasattr(self.memory, "get_chat_history_for_system_prompt"):
                        # 估算当前上下文字符数：system_prompt + memory_messages
                        context_chars = len(self._orchestrator_executor.system_prompt or "")
                        for mm in memory_messages:
                            context_chars += len(str(mm.get("content", "")))
                        cw = settings.agent.context_window
                        history_text = self.memory.get_chat_history_for_system_prompt(
                            total_context_chars=context_chars,
                            context_window=cw,
                            event_bus=self._event_bus,
                        )
                        if history_text:
                            memory_messages.append({
                                "role": "system",
                                "content": history_text,
                            })

                    # 注入相关任务经验到上下文
                    experience_context = self._build_experience_context()
                    if experience_context:
                        memory_messages.append({
                            "role": "system",
                            "content": experience_context,
                        })

                    agent_result = self._orchestrator_executor.run(
                        context=context,
                        user_text=user_input,
                        attachments=[],
                        memory_messages=memory_messages,
                        abort_check=abort_check,
                    )

                    if agent_result.is_timeout:
                        q.put({"type": "error", "content": agent_result.final_output})
                        result_holder["result"] = None
                    else:
                        self._validate_artifacts(agent_result)
                        result_holder["result"] = agent_result

                except Exception as exc:
                    error_str = str(exc)
                    cause_str = str(getattr(exc, '__cause__', '') or getattr(exc, '__context__', '') or '')
                    combined = error_str + cause_str
                    token_error_codes = (
                        "Arrearage", "QuotaExhausted", "InsufficientBalance", "AccountArrears",
                        "overdue-payment", "余额不足", "欠费", "额度不足",
                    )
                    if any(code in combined for code in token_error_codes):
                        q.put({"type": "llm_token_error", "content": "LLM模型服务账号Token余额不足，无法提供服务"})
                    else:
                        q.put({"type": "error", "content": error_str})
                finally:
                    self._current_run_context = None
                    self._event_bus.clear_queue()
                    try:
                        from agent.runtime.services.ask_service import get_ask_service
                        get_ask_service().clear_emit_fn(session_id=self.session_id)
                    except Exception:
                        pass
                    q.put({"type": "__done__"})

            current_ctx = contextvars.copy_context()
            t = threading.Thread(target=lambda: current_ctx.run(_run_loop), daemon=True)
            t.start()

            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield {"type": "heartbeat"}
                    continue

                if not isinstance(event, dict):
                    continue

                event_type = event.get("type", "")
                if event_type == "__done__":
                    break
                if event_type == "llm_token_error":
                    yield event
                    break
                if event_type == "error":
                    error_msg = event.get("content", "Unknown error")
                    yield event
                    break

                yield event

            agent_result: Optional[AgentResult] = result_holder.get("result")
            full_answer = agent_result.final_output if agent_result else ""
            full_reasoning = agent_result.reasoning if agent_result else ""
            full_tool_calls = []
            if agent_result and agent_result.tool_results:
                for tr in agent_result.tool_results:
                    full_tool_calls.append({
                        "tool_name": tr.name,
                        "tool_input": "",
                        "tool_output": tr.content,
                    })

            if not full_answer and full_tool_calls:
                last_done = next((tr["tool_output"] for tr in reversed(full_tool_calls) if tr["tool_output"]), "")
                if last_done:
                    full_answer = last_done[:500]

            if full_answer:
                yield {"type": "final_text", "content": full_answer}

            if full_answer:
                if hasattr(self.memory, "add_ai_message_with_trace"):
                    self.memory.add_ai_message_with_trace(full_answer, full_reasoning, full_tool_calls)
                elif hasattr(self.memory, "add_ai_message"):
                    self.memory.add_ai_message(full_answer)
                # 持久化对话历史
                if hasattr(self.memory, "save_chat_history"):
                    try:
                        self.memory.save_chat_history()
                    except Exception as e:
                        logger.warning("保存对话历史失败: %s", e)

            # 任务经验自动捕获（非阻塞，后台线程）
            if settings.task_experience.enabled and settings.task_experience.auto_capture:
                try:
                    from memory.task_experience import get_task_experience_capture
                    capture = get_task_experience_capture(self.llm_service)
                    if capture:
                        plan = self._last_loop_state.plan if self._last_loop_state else None
                        tool_results_list = agent_result.tool_results if agent_result else []
                        capture.on_task_complete(
                            session_id=self.session_id,
                            user_input=user_input,
                            plan=plan,
                            tool_results=tool_results_list,
                            final_output=full_answer,
                            execution_duration=time.time() - self._step_start_time if self._step_start_time else 0,
                        )
                except Exception as e:
                    logger.warning("Task experience capture failed (non-critical): %s", e)

            if hasattr(self.memory, "set_status_callback"):
                self.memory.set_status_callback(None)

            if agent_result and agent_result.is_timeout:
                logger.warning("NativeFloodAgent 流式执行超时")
            else:
                logger.info("NativeFloodAgent 流式执行成功")

        except Exception as e:
            logger.error("NativeFloodAgent 流式执行失败: %s", e)
            if hasattr(self.memory, "set_status_callback"):
                self.memory.set_status_callback(None)
            yield {"type": "reasoning", "content": f"抱歉，处理您的请求时出错了：{str(e)}"}
        finally:
            _active_input_var.set("")

    def run(self, user_input: str) -> str:
        """非流式运行（收集所有流式事件后返回最终回答）。"""
        full_answer = ""
        for event in self.stream(user_input):
            if event.get("type") == "final_text":
                full_answer = event.get("content", "")
            elif event.get("type") == "token" and not full_answer:
                full_answer += event.get("content", "")
        return full_answer or "抱歉，处理您的请求时未能生成回答。"

    def chat(self, message: str) -> str:
        return self.run(message)

    def chat_stream(self, message: str):
        yield from self.stream(message)

    def get_memory_summary(self) -> Dict[str, Any]:
        if hasattr(self.memory, "to_dict"):
            return self.memory.to_dict()
        return {}

    def clear_memory(self):
        if hasattr(self.memory, "clear"):
            self.memory.clear()
        logger.info("NativeFloodAgent 记忆已清空")
