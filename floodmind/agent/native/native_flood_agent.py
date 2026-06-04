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
from pathlib import Path
from typing import Any, Dict, List, Optional

from floodmind.agent.native.types import (
    AgentLoopState,
    AgentResult,
    Attachment,
    ExecutionPlan,
    RunContext,
)
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.native.event_bus import EventBus, StepEventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.planner import Planner
from floodmind.agent.native.tool_runtime import native_from_agent_tool

from floodmind.config.settings import settings

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
    # ── Prompt 拆分为三部分，以最大化 prompt cache 前缀命中率 ──
    #
    # msg[0] STATIC_GLOBAL: 角色/工作流/规则/工具目录/技能目录 — 跨 session 永不变化
    # msg[1] STATIC_PER_PROJECT: AGENTS.md 项目/全局规则 — 仅 AGENTS.md 变更时变化
    # msg[2] STATIC_PER_SESSION: 系统时间 + 会话路径/环境 — 每个 session 不同
    #
    # 顺序：STATIC_GLOBAL → STATIC_PER_PROJECT → STATIC_PER_SESSION
    # 这样 DashScope/OpenAI 的 prompt cache prefix 能命中 msg[0]（甚至 msg[0]+msg[1]）

    SYSTEM_PROMPT_STATIC_GLOBAL = """你是大水云科技开发的FloodMind。

## 角色职责
1. 分析用户意图和最终目标
2. 规划任务步骤（复杂任务建议先 create_plan）
3. 处理无顺序依赖时启动子代理
4. 汇总结果并回答用户

## 工作方式
你是全能执行者，拥有所有工具，可以自己完成任何任务。你的核心优势是掌握完整对话上下文，无需压缩信息传递。但是为了提高工作效率，建议合理使用子代理

### 通用原则
- 如问好之类的简单问题直接回答就行
- 不要把任务复杂化
- 不要过度思考
- 最终产物检查：对任何最终产物，必须完整查看产物内容并对比用户意图/上传文件，确保产物质量

### 何时自己完成
- 写报告、写文档等需要丰富上下文和连续思考的任务 → 自己做，不要委派
- 简单的文件读写、脚本执行 → 自己做
- 只读查询、简单问答、小文本整理 → 自己做

### 何时使用子代理
- 需要并行执行多个独立子任务（如同时搜索多个话题）→ 用 ParallelTask
- 耗时较长的脚本/模型运行 → 用 Task
- 委派技巧：1、需要依赖对话上下文和连续思考的任务，委派时要将对话上下文和核心要点一起告知子代理，2、不要过度限制子代理，只需要明确的任务描述、用户提供的数据和文件内容\\路径、最终产物是什么即可。


## 定时任务处理
当用户表达"每天、明天、某个时间、定时、自动、后台执行、提前安排任务"等需求时：
1. 调用 `create_scheduled_task` 写入任务列表，不要立即执行业务任务。
2. `command` 只保留未来真正要执行的业务任务，必须去掉"每天/定时/几点执行"等调度表达，避免后台执行时再次创建定时任务。
3. 每日重复任务使用 `repeat="daily"` 和 `run_time="HH:MM"`；一次性任务使用 `repeat="none"` 和 `scheduled_at`。
4. 任务默认绑定当前会话，用户后续可从前端查看该任务生成的新增文件。
5. 用户询问已有定时任务时调用 `list_scheduled_tasks`；用户取消定时任务时调用 `cancel_scheduled_task`。

## 知识入库处理
当用户提供了具体的业务文档或用户询问了许多专业知识并对你的回答没有异议时，你可以使用知识库 MCP 工具将业务文档的知识或者历史对话中的相关知识整理入库：
1. 如果需要创建知识库，调用 `mcp:knowledge:kb_create_knowledge_base`
2. 如果用户提供了文件路径或上传文件，调用 `mcp:knowledge:kb_upload_document(kb_id=..., file_path=...)`
3. 上传后调用 `mcp:knowledge:kb_process_document(doc_id=...)` 触发解析
4. 入库成功后，明确告知用户后续可通过 `mcp:metahuman:mh_knowledge_query` 检索

## 用户偏好处理
当用户表达长期偏好、规则或习惯时（如"以后都用PNG格式"、"不要生成PDF"）：
1. 先确认用户意图：此偏好仅本次对话生效，还是所有对话都生效？
2. 仅本次对话 → 调用 `MemoryAdd` 写入会话记忆
3. 所有对话 → 进一步确认作用域：
   - 仅本项目 → `UpdateProjectInstructions(scope="project")`
   - 全局所有项目 → `UpdateProjectInstructions(scope="global")`
4. **写入前必须向用户展示将要写入的内容，等待用户确认后再执行 `UpdateProjectInstructions`**
5. 写入后告知用户：此偏好已持久化，将在后续所有对话中自动生效

## 执行工具细节
- 调用工具时一次只传一个参数：例如要查看两个skill时，应该是`GetSkill(skill1)`，等待返回结果，再进行`GetSkill(skill2)`，等待返回结果
- excel的sheet命名字符最长允许31个字符，所以stationCode太长时，sheet_name可能会被截断
- Bash 可执行任何 shell 命令，不限于 Python；支持 python、node、npm 等运行时
- skill 指定非 Python 技术栈时（如 JavaScript），用 Write 写脚本文件，再用 Bash 执行
- 执行前确保依赖已安装；缺失时用 pip install / npm install 安装

## 并行子代理规则
使用 ParallelTask 并行委派多个独立任务时：
- 各任务必须互不依赖（不读写同一文件、不依赖彼此的输出产物）
- 有依赖关系的步骤仍用 Task 串行委派
- 不要对需要用户确认权限的任务使用并行委派
- 典型场景：并行搜索多个信息源，并行运行多个独立脚本

## 敖江流域子任务编码
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~水动力模型区间断面预报`、`水动力模型区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`
- 详细信息查看 aojiang-hydro SKILL.md文档

## 可用 skills
{skill_catalog}

## 可用工具
{tool_descriptions}

## 工作流
### 1. 分析目标
先明确：
1. 用户最终要什么交付物
2. 当前输入属于原始数据、中间结果还是最终结果
3. 当前缺的是哪一个阶段
4. 当前任务是基于之前的任务成果继续还是开启新的任务
5. 若是基于之前的任务成果，绝对不能重跑之前的任务，必须严格按照已有成果开展工作

### 2. skill使用规则
如果任务对应某个业务 skill，使用skill前必须先调用 `GetSkill` 查看详细说明，再决定下一步。

### 3. 选择执行方式
- 需要丰富上下文的任务（写报告、综合分析等）→ 自己直接完成
- 简单的单步任务（跑脚本、生成文件等）→ 自己直接完成或用 Task 委派
- 有多个独立子任务 → 用 ParallelTask 并行
- 复杂多步骤流程 → 建议 create_plan 规划后按步骤执行

### 4. 结合校验继续推进
只有当本轮任务明确承诺了文件产物时，才在流程结束后执行代码级最终文件存在性检查。
如果最终文件检查明确指出缺失文件，优先按缺失文件结果继续，不要自己重新写成模糊任务。

### 5. 整理最终回答
最终只向用户总结：
1. 已完成什么
2. 生成了哪些最终文件
3. 如果未完成，还缺什么

## 工作原则
1. 你是全能执行者，可以自己完成任何任务，也可以启动子代理辅助
2. 如果有相关 skill，先使用`GetSkill`查 skill，再决定下一步
3. 对子代理只传当前这一步的执行指令，不要传过长内容
4. 严禁把超长 JSON 直接塞进任务描述
5. 必须严格遵循 SKILL.md 及相关文档
6. 不要过度解读任务
7. 编写或者修改长报告、长文档等任务时，可以适当将报告、文档拆分为几个独立部分（按章节、按内容拆分等），然后将拆分后的各部分委派给多个子代理并行处理，最后汇总得到最终报告、文档；注意委派子代理的时候交代清楚需要阅读的参考资料路径、是否需要进行网络搜索、知识检索补充内容等。
8. 读取、分析多个文件时，可以适当委派多个子代理并行处理

## 产物意图判定
在决定是否生成面向用户的持久化文件时，请根据用户意图自行判断：
- 用户明确要求"生成、导出、保存、下载、报告、Excel、Word、PDF、图片"等文件时 → 生成文件作为最终交付物
- 用户只要求"计算、分析、查询、告诉我结果、看看数据"等 → 最终交付物默认为文字答案，不需要额外生成文件
- 不要主动为用户未要求的文件类型生成报告或导出文件
- 如果用户只要求文字结果，即使工具天然输出文件，也以文字汇总为主，文件仅作为可下载附件

## 文档声明
- 在生成的word、excel、PDF等文件任务中，必须在文件内容最后加上"以上内容由FloodMind生成，请认真核对内容正确性"文字。
- 生成内容复杂、选题不明等word、PDF等文件时，可以先使用 doc-coauthoring 技能向用户确定必要信息。

## 输出规范
- 最终输出不要包含会话环境内部信息
- 标准 Markdown 格式输出
"""

    SYSTEM_PROMPT_PROJECT_TEMPLATE = """{project_context}"""

    SYSTEM_PROMPT_SESSION_TEMPLATE = """## 当前系统时间
{current_time_context}

## 当前会话信息
{session_env}"""

    # 兼容旧代码引用（合并模板，保留以便需要时使用）
    SYSTEM_PROMPT = "{project_context}\n" + SYSTEM_PROMPT_STATIC_GLOBAL + SYSTEM_PROMPT_SESSION_TEMPLATE


    SPECIALIST_STATIC_GLOBAL = """你是 FloodMind 子代理，负责完成主代理分配的子任务。

## 你的职责
1. 执行主代理分配的子任务
2. 根据需要运行 skill 脚本
3. 编写并执行临时 Python 脚本

## 执行原则
- 完成分配给你的任务，但如果任务内容不够充实，主动使用 WebSearch/WebFetch 搜索补充
- 不要仅依赖 prompt 中提供的信息，主动搜索和查阅使结果更丰富
- 做完立即返回，不扩展上下游，不规划后续步骤
- 如果指令缺文件、缺参数、缺前置产物，明确指出缺什么
- 使用skill前必须先调用 `GetSkill` 查看详细说明，再决定下一步

## 执行工具细节
- 调用工具时一次只传一个参数
- Bash 可执行任何 shell 命令，不限于 Python；支持 python、node、npm 等运行时
- skill 指定非 Python 技术栈时，用 Write 写脚本文件，再用 Bash 执行
- 所有需要传递路径的工具都只接收绝对路径

## 强约束
- 不要猜测或杜撰 skill 中未声明的脚本、参数或字段
- 不要把超长 JSON 直接塞进工具参数
- 不要根据聊天文本手工搬运大数组；优先从原始文件读取
- 不要继续规划下游步骤；你只完成当前分配的任务
- 如果任务目标已经达成，不要重复调用工具

## 可使用工具
1. `GetSkill`
2. `Glob`
3. `Grep`
4. `Read`
5. `Write`
6. `Edit`
7. `Bash`
8. `WebSearch`（主动搜索补充内容，使结果更充实）
9. `WebFetch`（获取网页详细内容）
10. `mcp:metahuman:mh_knowledge_query`（检索知识库补充专业内容）
11. `mcp:knowledge:kb_upload_document`（将文档上传到知识库）

## 可使用skills
{skill_catalog}

## 输出要求
- 简洁说明本次任务是否完成
- 明确返回直接结果，如生成文件路径、读取/搜索结果、关键输出摘要
- 不要给出下一步建议，不要说明后续如何使用，由主代理决定后续动作
"""

    SPECIALIST_PROJECT_TEMPLATE = """{project_context}"""

    SPECIALIST_SESSION_TEMPLATE = """## 当前会话信息
{session_env}"""

    # 兼容旧代码引用
    EXECUTION_SPECIALIST_PROMPT = "{project_context}\n" + SPECIALIST_STATIC_GLOBAL + SPECIALIST_SESSION_TEMPLATE

    _ARTIFACT_EXTENSIONS = {".json", ".csv", ".xlsx", ".xls", ".docx", ".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg"}

    def __init__(
        self,
        llm_service=None,
        memory=None,
        session_id: str = "",
        enable_search: bool = False,
        enable_reasoning: bool = False,
        agent_type: str = "build",
        **kwargs,
    ):
        self.llm_service = llm_service
        self.memory = memory
        self.session_id = session_id
        self._enable_search = enable_search
        self._enable_reasoning = enable_reasoning
        self._agent_type = agent_type

        from floodmind.agent.agent_registry import get_agent
        self._agent_info = get_agent(agent_type) or get_agent("build")

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
    def _build_tool_descriptions(registry) -> str:
        """从工具注册表动态生成工具描述列表。"""
        if not registry:
            return "- (无工具注册)"
        lines = []
        for tool in registry.all():
            name = tool.name
            desc = getattr(tool, "description", "") or ""
            short = desc.split("。")[0].split(".")[0][:80] if desc else ""
            if short:
                lines.append(f"- `{name}`：{short}")
            else:
                lines.append(f"- `{name}`")
        return "\n".join(lines)

    @staticmethod
    def _warmup_chronos():
        with NativeFloodAgent._chronos_warmup_lock:
            if NativeFloodAgent._chronos_warmup_done:
                logger.info("Chronos-2 预热已完成，跳过重复预热")
                return
            NativeFloodAgent._chronos_warmup_done = True

        def _warmup():
            try:
                from floodmind.skills.chronos_pipeline import get_pipeline
                get_pipeline()
            except Exception as e:
                logger.warning(f"Chronos-2 预热失败（不影响功能）: {e}")
                with NativeFloodAgent._chronos_warmup_lock:
                    NativeFloodAgent._chronos_warmup_done = False
        t = threading.Thread(target=_warmup, daemon=True, name="chronos-warmup-native")
        t.start()

    def _init_tools(self) -> None:
        from floodmind.tools import (
            get_skill, exec_bash,
            knowledge_search, add_knowledge,
            web_search, fetch_webpage, add_memory, search_memory,
            update_project_instructions,
            create_scheduled_task, list_scheduled_tasks, cancel_scheduled_task,
            set_rag_config, set_memory_instance, reset_retry_guard,
        )
        from floodmind.tools.agent_tool import ToolRegistry as _GlobalToolRegistry
        from floodmind.memory.task_experience import get_task_experience_capture
        from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, PermissionRule, ToolPermissionPolicy
        from floodmind.agent.runtime.services.permission_service import PermissionService, set_permission_service
        from floodmind.agent.runtime.services.ask_service import get_ask_service, set_ask_service
        from floodmind.agent.runtime.services.path_service import PathService, set_path_service
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
        from floodmind.skills import SKILL_REGISTRY
        from floodmind.config.settings import settings as _settings

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

        from floodmind.tools.agent_tool import set_permission_manager
        set_permission_manager(perm_svc)

        from floodmind.tools.file_tools import Glob_tool, Grep_tool, Read_tool, Write_tool, Edit_tool

        all_tools = [
            Glob_tool, Grep_tool, Read_tool, Write_tool, Edit_tool,
            get_skill, exec_bash,
            search_memory, update_project_instructions,
            create_scheduled_task, list_scheduled_tasks, cancel_scheduled_task,
        ]
        # 从全局 ToolRegistry 获取任务经验工具
        for _tname in ("SearchTaskExperience", "AddTaskExperience",
                        "BrowseExperienceTree", "DrillDownExperience"):
            _t = _GlobalToolRegistry.get(_tname)
            if _t:
                all_tools.append(_t)
        if self._enable_search:
            all_tools.append(web_search)
            all_tools.append(fetch_webpage)

        self._skill_catalog = "\n".join(
            f"- {s.name}: {s.description}"
            + (f" (v{s.version})" if s.version and s.version != "1.0" else "")
            + (f" [provides: {', '.join(s.provides_tools)}]" if s.provides_tools else "")
            for s in SKILL_REGISTRY
        ) + "\n- GetSkill: 按需获取任意技能的完整参数说明"

        self._orchestrator_registry.register_tools(all_tools)
        self._specialist_registry.register_tools(all_tools)

        # ── MCP 外部工具接入 ────────────────────────────
        _mcp_servers = _settings.mcp.servers if hasattr(_settings, 'mcp') else []
        self._mcp_pool = None
        self._mcp_pool_lock = threading.Lock()
        if _mcp_servers:
            try:
                from floodmind.agent.mcp_client import get_mcp_client_pool
                self._mcp_pool = get_mcp_client_pool()
                connected = self._mcp_pool.connect_all(_mcp_servers)
                if connected > 0:
                    mcp_tools_registered = 0
                    for server_name, conn in self._mcp_pool._connections.items():
                        mcp_tools_registered += self._register_mcp_tools(server_name, conn, self._orchestrator_registry)
                    logger.info("MCP: %d 个外部工具已注册", mcp_tools_registered)
            except Exception as e:
                logger.warning("MCP 外部工具加载失败: %s", e)

        # LoadMcpServer: 运行时动态接入 MCP Server
        self._orchestrator_registry.register(ToolSpec(
            name="LoadMcpServer",
            description="运行时动态接入外部 MCP Server，将其工具注册到当前 Agent。接入后即可直接调用 mcp:<server>:<tool> 格式的工具。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "MCP Server 名称（唯一标识）"},
                    "transport": {"type": "string", "enum": ["sse", "stdio"], "description": "传输方式"},
                    "url": {"type": "string", "description": "SSE endpoint URL（transport=sse 时必填）"},
                    "command": {"type": "string", "description": "启动命令（transport=stdio 时必填）"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "命令参数（optional）"},
                    "env": {"type": "object", "description": "环境变量（optional）"},
                },
                "required": ["name", "transport"],
            },
            func=self._handle_load_mcp_server,
            is_readonly=False,
            is_destructive=False,
            is_concurrency_safe=True,
            permission_policy=ToolPermissionPolicy(policy_type="network"),
        ))

        self._orchestrator_registry.register(ToolSpec(
            name="create_plan",
            description="复杂任务建议先规划。创建结构化执行计划，明确用户意图、预期交付物和执行步骤。简单任务无需调用。",
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
                                "needs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "本步骤依赖的前置步骤 step_id 列表",
                                },
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
            name="SubAgent",
            description="启动子代理辅助完成子任务。适用于：耗时脚本运行、独立子任务、需要并行处理的搜索等。注意：需要丰富上下文的写作/报告任务应自己做，不要委派。若已明确要复用某个 skill，同时传入 skill_name。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "交给子代理的明确任务说明，应尽量具体、可执行"},
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
            name="ParallelTask",
            description="并行启动多个子代理。各任务必须互不依赖（不读写同一文件、不依赖彼此的输出）。有依赖关系的步骤仍用 Task 串行委派。",
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "可并行执行的任务列表，每个元素含 task(任务说明)、skill_name(可选)、step_key(可选，对应 create_plan 中的步骤ID)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "交给子代理的明确任务说明"},
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
            # 兼容外部传入 LLM service 的旧路径（如 web_server 传入的 get_qwen_llm_service）
            if hasattr(self.llm_service, "enable_reasoning") and self.llm_service.enable_reasoning:
                thinking = True
                self._orchestrator_extra_body = {"enable_thinking": True}
            else:
                thinking = False
                self._orchestrator_extra_body = {}

            self._model_client = ModelClient(
                api_key=getattr(self.llm_service, "api_key", ""),
                base_url=getattr(self.llm_service, "base_url", ""),
                model_name=getattr(self.llm_service, "model_name", ""),
                temperature=getattr(self.llm_service, "temperature", 0.3),
                max_tokens=getattr(self.llm_service, "max_tokens", 4096),
                enable_thinking=thinking,
            )
            return

        # 默认路径：从 settings.json 读取
        from floodmind.config.model_presets import get_default_model_key, get_preset

        model_key = get_default_model_key()
        preset = get_preset(model_key)
        if not preset:
            raise ValueError(f"未知的模型预设: {model_key}")

        enable_thinking = bool(self._enable_reasoning and preset.get("supports_reasoning"))
        if enable_thinking:
            self._orchestrator_extra_body = {"enable_thinking": True}
        else:
            self._orchestrator_extra_body = {}

        self._model_client = ModelClient.from_settings_with_preset(
            model_key=model_key,
            enable_reasoning=enable_thinking,
        )

    def _init_executors(self) -> None:
        from floodmind.agent.context_runtime import ContextRuntime

        self._context_runtime = ContextRuntime(
            context_window=settings.agent.context_window,
        )
        self._context_runtime.prefetch()

        project_context = self._context_runtime.load_project_rules()
        current_time_context = ContextRuntime.load_current_time_static()

        output_dir = self._get_output_dir()
        upload_dir = self._get_upload_dir()
        os_name = "Windows" if os.name == "nt" else "Linux"
        shell_name = "powershell" if os.name == "nt" else "bash"
        session_env = (
            f"会话输出目录: {output_dir}\n"
            f"上传目录: {upload_dir}\n"
            f"操作系统: {os_name}\n"
            f"Shell: {shell_name}\n"
            f"路径风格: {os_name}"
        )

        self._project_context = project_context
        self._current_time_context = current_time_context
        self._session_env = session_env

        tool_descriptions = self._build_tool_descriptions(self._orchestrator_registry)

        # Use agent-specific prompt if defined, otherwise split into three parts for cache reuse.
        agent_prompt = getattr(self._agent_info, "prompt", "") if self._agent_info else ""
        if agent_prompt:
            # 旧路径：agent_info 自定义了整套 prompt，仍然合并为单条
            orchestrator_prompts = [
                agent_prompt.format(
                    skill_catalog=self._skill_catalog,
                    current_time_context=current_time_context,
                    project_context=project_context,
                    session_env=session_env,
                    tool_descriptions=tool_descriptions,
                )
            ]
        else:
            # 新路径：拆分为三条 system messages
            orchestrator_prompts = [
                self.SYSTEM_PROMPT_STATIC_GLOBAL.format(
                    skill_catalog=self._skill_catalog,
                    tool_descriptions=tool_descriptions,
                ),
                self.SYSTEM_PROMPT_PROJECT_TEMPLATE.format(project_context=project_context),
                self.SYSTEM_PROMPT_SESSION_TEMPLATE.format(
                    current_time_context=current_time_context,
                    session_env=session_env,
                ),
            ]

        specialist_prompts = [
            self.SPECIALIST_STATIC_GLOBAL.format(skill_catalog=self._skill_catalog),
            self.SPECIALIST_PROJECT_TEMPLATE.format(project_context=project_context),
            self.SPECIALIST_SESSION_TEMPLATE.format(session_env=session_env),
        ]

        self._orchestrator_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=50,
            extra_body=self._orchestrator_extra_body,
            system_prompts=orchestrator_prompts,
            tools_schema=self._orchestrator_registry.tools_schema(),
            tool_registry=self._orchestrator_registry,
            require_plan_before_delegate=False,
        )

        self._specialist_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=50,
            system_prompts=specialist_prompts,
            tools_schema=self._specialist_registry.tools_schema(),
            tool_registry=self._specialist_registry,
        )

    def _register_mcp_tools(self, server_name: str, conn, registry) -> int:
        """将 MCP Server 的工具注册到指定 registry，返回注册数量"""
        count = 0
        for mt in conn.list_tools():
            mcp_tool_name = f"mcp:{server_name}:{mt.get('name', '')}"
            input_schema = mt.get("inputSchema", {})
            registry.register(ToolSpec(
                name=mcp_tool_name,
                description=f"[MCP:{server_name}] {mt.get('description', '')}",
                parameters={
                    "type": "object",
                    "properties": input_schema.get("properties", {}),
                    "required": input_schema.get("required", []),
                },
                func=lambda args, sn=server_name, tn=mt.get('name', ''): self._mcp_pool.call_tool(f"mcp:{sn}:{tn}", args) if self._mcp_pool else "MCP 连接已断开",
                is_readonly=False,
                is_destructive=True,
                is_concurrency_safe=True,
                permission_policy=ToolPermissionPolicy(policy_type="network"),
            ))
            count += 1
        return count

    def _rebuild_system_prompts(self) -> None:
        """当 skill catalog / tool 列表刷新时，只需重建 STATIC_GLOBAL 部分
        （PROJECT 和 SESSION 部分是稳定的）。"""
        if not self._orchestrator_executor or not self._specialist_executor:
            return

        pc = getattr(self, "_project_context", "")
        ctc = getattr(self, "_current_time_context", "")
        se = getattr(self, "_session_env", "")

        agent_prompt = getattr(self._agent_info, "prompt", "") if self._agent_info else ""
        if agent_prompt:
            # 旧路径：agent_info 自定义 prompt，仍合并为单条
            tool_descriptions = self._build_tool_descriptions(self._orchestrator_registry)
            merged = agent_prompt.format(
                skill_catalog=self._skill_catalog,
                current_time_context=ctc,
                project_context=pc,
                session_env=se,
                tool_descriptions=tool_descriptions,
            )
            self._orchestrator_executor.system_prompts = [merged]
        else:
            tool_descriptions = self._build_tool_descriptions(self._orchestrator_registry)
            new_global = self.SYSTEM_PROMPT_STATIC_GLOBAL.format(
                skill_catalog=self._skill_catalog,
                tool_descriptions=tool_descriptions,
            )
            # 只替换第 0 条（STATIC_GLOBAL），保留 PROJECT 和 SESSION 不变
            prompts = list(self._orchestrator_executor.system_prompts)
            prompts[0] = new_global
            self._orchestrator_executor.system_prompts = prompts

        # 子代理的 STATIC_GLOBAL 也需要刷新 skill catalog
        new_spec_global = self.SPECIALIST_STATIC_GLOBAL.format(skill_catalog=self._skill_catalog)
        spec_prompts = list(self._specialist_executor.system_prompts)
        spec_prompts[0] = new_spec_global
        self._specialist_executor.system_prompts = spec_prompts

    def refresh_skills(self) -> None:
        """刷新 skill 注册表并重建 Agent 的 system prompt"""
        from floodmind.skills import refresh_skill_registry, SKILL_REGISTRY as _reg
        refresh_skill_registry()
        new_catalog = "\n".join(
            f"- {s.name}: {s.description}"
            + (f" (v{s.version})" if s.version and s.version != "1.0" else "")
            + (f" [provides: {', '.join(s.provides_tools)}]" if s.provides_tools else "")
            for s in _reg
        ) + "\n- GetSkill: 按需获取任意技能的完整参数说明"
        self._skill_catalog = new_catalog
        self._rebuild_system_prompts()
        logger.info("NativeFloodAgent 技能已刷新: catalog=%d chars", len(self._skill_catalog))
        # 注册 B3 经验→Skill 生成回调
        try:
            import floodmind.memory.task_experience as _te
            _te._on_skill_generated = self.refresh_skills
        except Exception:
            pass

    def _handle_load_mcp_server(self, name: str = "", transport: str = "sse", url: str = "", command: str = "", args: Any = "", env: Any = "") -> str:
        """运行时动态接入 MCP Server 的工具处理函数"""
        if not name:
            return "错误: 必须提供 name 参数"
        if transport not in ("sse", "stdio"):
            return f"错误: transport 必须是 sse 或 stdio，收到: {transport}"
        if transport == "sse" and not url:
            return "错误: SSE transport 需要 url 参数"
        if transport == "stdio" and not command:
            return "错误: stdio transport 需要 command 参数"

        args_list = args if isinstance(args, list) else []
        env_dict = env if isinstance(env, dict) else {}

        server_config = {
            "name": name,
            "transport": transport,
            "url": url if transport == "sse" else "",
            "command": command if transport == "stdio" else "",
            "args": args_list,
            "env": env_dict,
        }

        try:
            from floodmind.agent.mcp_client import get_mcp_client_pool
            with self._mcp_pool_lock:
                pool = self._mcp_pool or get_mcp_client_pool()
                if self._mcp_pool is None:
                    self._mcp_pool = pool

            count = pool.connect_and_register(server_config, self._orchestrator_registry)

            # 同步注册到 specialist registry（持锁迭代）
            with pool._lock:
                conn = pool._connections.get(name)
            if conn:
                self._register_mcp_tools(name, conn, self._specialist_registry)

            return f"MCP Server '{name}' 已接入，{count} 个工具已注册。使用 mcp:{name}:<tool_name> 调用。"

        except Exception as e:
            logger.error("LoadMcpServer 失败: %s", e)
            return f"接入 MCP Server '{name}' 失败: {e}"

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
                "needs": raw_step.get("needs", []) or [],
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
            old_plan = self._last_loop_state.plan
            if old_plan and old_plan.steps and any(
                s.get("status") not in (None, "pending") for s in old_plan.steps
            ):
                logger.warning("覆盖已有执行计划（旧计划有 %d 步，含非 pending 步骤）", len(old_plan.steps))
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

        specialist_prompts = list(self._specialist_executor.system_prompts) if self._specialist_executor else []

        results: Dict[str, Dict[str, Any]] = {}

        # 判断是否需要 DAG 调度
        use_dag = False
        if self._last_loop_state is not None and self._last_loop_state.plan is not None:
            plan = self._last_loop_state.plan
            for task_def in tasks:
                step_key = task_def.get("step_key") or ""
                plan_step = plan.find_step(step_key)
                if plan_step and plan_step.get("needs"):
                    use_dag = True
                    break

        if use_dag and self._last_loop_state is not None and self._last_loop_state.plan is not None:
            return self._run_dag_batches(tasks, context, specialist_prompts, max_concurrent, results)

        # 原有扁平并行逻辑
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
                system_prompts=list(specialist_prompts),
                tools_schema=self._specialist_registry.tools_schema(),
                tool_registry=self._specialist_registry,
            )

            specialist_input = self._build_specialist_user_input(task_text, skill_name)

            from floodmind.agent.native.artifact_watcher import ArtifactWatcher
            local_output = context.output_dir if context else None
            local_watcher = ArtifactWatcher(output_dir=str(local_output)) if local_output else None

            with self._artifact_lock:
                if local_watcher:
                    local_watcher.take_snapshot()

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
            if local_watcher:
                new_artifacts = local_watcher.detect_new_artifacts()
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

    def _run_dag_batches(self, tasks: list, context: RunContext, specialist_prompts: list, max_concurrent: int, results: Dict[str, Any]) -> str:
        """按 DAG 拓扑层级分批并行执行"""
        plan = self._last_loop_state.plan

        # 构建步骤查找表：step_key → task_def
        step_tasks: Dict[str, Dict[str, Any]] = {}
        for task_def in tasks:
            step_key = task_def.get("step_key") or ""
            if step_key:
                step_tasks[step_key] = task_def

        batches = plan.get_batches()
        logger.info("[DAG并行] 总步骤=%d, 批次=%d", len(tasks), len(batches))

        for batch_idx, batch in enumerate(batches):
            logger.info("[DAG并行] 批次 %d/%d: steps=%s", batch_idx + 1, len(batches), batch)

            def _run_dag_step(step_id: str) -> tuple:
                task_def = step_tasks.get(step_id, {})
                task_text = (task_def.get("task") or "").strip()
                skill_name = (task_def.get("skill_name") or "").strip()

                self._event_bus.emit_workflow_step(
                    step_key=step_id,
                    status="running",
                    title=task_text[:60],
                    detail=skill_name or "",
                )

                step_event_bus = StepEventBus(self._event_bus, step_id)
                specialist_executor = NativeAgentExecutor(
                    model_client=self._model_client,
                    tool_executor=self._tool_executor,
                    event_bus=step_event_bus,
                    message_builder=MessageBuilder(),
                    max_iterations=50,
                    system_prompts=list(specialist_prompts),
                    tools_schema=self._specialist_registry.tools_schema(),
                    tool_registry=self._specialist_registry,
                )

                specialist_input = self._build_specialist_user_input(task_text or f"执行步骤: {step_id}", skill_name)

                from floodmind.agent.native.artifact_watcher import ArtifactWatcher
                local_output = context.output_dir if context else None
                local_watcher = ArtifactWatcher(output_dir=str(local_output)) if local_output else None
                if local_watcher:
                    local_watcher.take_snapshot()

                try:
                    result = specialist_executor.run(
                        context=context,
                        user_text=specialist_input,
                        attachments=context.attachments,
                        memory_messages=[],
                        abort_check=context.abort_check,
                    )
                except Exception as e:
                    logger.error("[DAG并行] 步骤 %s 异常: %s", step_id, e)
                    self._event_bus.emit_workflow_step(step_key=step_id, status="error", title=task_text[:60], outcome=str(e)[:200])
                    plan_step = plan.find_step(step_id) if plan else None
                    if plan_step:
                        plan_step["status"] = "error"
                        plan_step["error_message"] = str(e)[:200]
                    return (step_id, {"status": "error", "summary": str(e)[:200], "artifacts": []})

                output = result.final_output or ""
                artifacts = []
                if local_watcher:
                    new_artifacts = local_watcher.detect_new_artifacts()
                    artifacts = [a.file_path for a in new_artifacts]

                has_tool_success = any(tr.status == "completed" for tr in result.tool_results) if result.tool_results else False
                step_status = "completed" if (output or has_tool_success or artifacts) else "error"

                if plan:
                    plan_step = plan.find_step(step_id)
                    if plan_step:
                        plan_step["status"] = step_status

                self._event_bus.emit_workflow_step(
                    step_key=step_id,
                    status=step_status,
                    title=task_text[:60],
                    outcome=output[:200] if output else "",
                )
                return (step_id, {
                    "status": step_status,
                    "summary": output[:500] if output else "",
                    "artifacts": artifacts,
                })

            max_workers = max(1, min(max_concurrent or 3, 5))
            with ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as pool:
                futures = {pool.submit(_run_dag_step, step_id): step_id for step_id in batch}
                for future in as_completed(futures):
                    try:
                        step_id, result_data = future.result()
                        results[step_id] = result_data
                    except Exception as e:
                        step_id = futures[future]
                        results[step_id] = {"status": "error", "summary": str(e)[:200], "artifacts": []}

        payload = {
            "stage": "dag_delegation",
            "stage_label": "DAG Parallel Execution",
            "result_type": "intermediate",
            "status": "completed" if all(r.get("status") != "error" for r in results.values()) else "partial",
            "user_goal": _active_input_var.get(),
            "tasks_count": len(tasks),
            "batches": len(batches),
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
                "如果当前任务明确要求复用该 skill，优先使用 Bash 执行 skill 中的脚本，遇到参数错误时再调用 `GetSkill` 查看其脚本与参数，再执行。",
            ])

        return "\n".join(lines).strip()

    @staticmethod
    def _set_session_context(session_id: str, output_dir: str) -> None:
        try:
            from floodmind.tools.base_tools import set_session_context
            set_session_context(session_id, output_dir)
        except Exception as e:
            logger.warning("Failed to set session context: %s", e)

    def _on_permission_ask(self, tool_name: str, tool_input: Dict[str, Any], reason: str) -> bool:
        from floodmind.agent.runtime.services.ask_service import get_ask_service
        call_id = str(tool_input.get("__call_id", "")) if isinstance(tool_input, dict) else ""
        clean_input = {k: v for k, v in tool_input.items() if k != "__call_id"} if isinstance(tool_input, dict) else tool_input
        bridge = get_ask_service()
        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest
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
            from floodmind.memory.task_experience import get_task_experience_store
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
            data_dir = os.environ.get('DATA_DIR', str(Path.cwd() / "data"))
            output_dir = os.path.join(data_dir, "sessions", self.session_id, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            return output_dir
        try:
            from floodmind.tools.base_tools import get_current_session_output_dir
            d = get_current_session_output_dir()
            if d:
                return d
        except Exception:
            pass
        return os.path.abspath(os.path.join("data", "agent_state"))

    def _get_upload_dir(self) -> str:
        if self.session_id:
            data_dir = os.environ.get('DATA_DIR', str(Path.cwd() / "data"))
            upload_dir = os.path.join(data_dir, "sessions", self.session_id, "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            return upload_dir
        return ""

    def stream(self, user_input: str, enable_reasoning: bool = False, user_message: str = "", attachments: Optional[List[Attachment]] = None, abort_check: Optional[Any] = None):
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
                        attachments=attachments or [],
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

                    saved_extra = getattr(self._orchestrator_executor, "extra_body", None)
                    if enable_reasoning:
                        self._orchestrator_executor.extra_body = {"enable_thinking": True}
                    else:
                        self._orchestrator_executor.extra_body = {}

                    self._event_bus.set_queue(q)

                    from floodmind.agent.runtime.services.ask_service import get_ask_service
                    ask_service = get_ask_service()
                    ask_service.set_emit_fn(lambda event: self._event_bus.emit(event), session_id=self.session_id)

                    experience_context = self._build_experience_context()
                    history_text = ""
                    if hasattr(self.memory, "get_chat_history_for_system_prompt"):
                        context_chars = len(self._orchestrator_executor.system_prompt or "")
                        cw = settings.agent.context_window
                        history_text = self.memory.get_chat_history_for_system_prompt(
                            total_context_chars=context_chars,
                            context_window=cw,
                            event_bus=self._event_bus,
                        ) or ""

                    memory_messages = []
                    if experience_context:
                        memory_messages.append({
                            "role": "system",
                            "content": experience_context,
                        })
                    if history_text:
                        memory_messages.append({
                            "role": "system",
                            "content": history_text,
                        })
                    if not memory_messages:
                        if hasattr(self.memory, "get_openai_messages"):
                            memory_messages = self.memory.get_openai_messages()
                        elif hasattr(self.memory, "get_full_messages"):
                            memory_messages = self._message_builder.build_memory_messages(
                                self.memory.get_full_messages()
                            )
                        elif hasattr(self.memory, "get_messages"):
                            memory_messages = self._message_builder.build_memory_messages(
                                self.memory.get_messages()
                            )

                    agent_result = self._orchestrator_executor.run(
                        context=context,
                        user_text=user_input,
                        attachments=attachments or [],
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
                    if saved_extra is not None:
                        self._orchestrator_executor.extra_body = saved_extra
                    self._event_bus.clear_queue()
                    try:
                        from floodmind.agent.runtime.services.ask_service import get_ask_service
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
                    from floodmind.memory.task_experience import get_task_experience_capture
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

                # 反馈回写：任务成功则对引用的经验 +success_count
                try:
                    if full_answer and agent_result and agent_result.final_output:
                        from floodmind.memory.task_experience import get_task_experience_store
                        store = get_task_experience_store()
                        all_leaves = store.tree.get_all_leaves()
                        is_success = not any(tr.status == "error" for tr in (agent_result.tool_results or []))
                        for leaf in all_leaves:
                            path_str = "/".join(leaf.path)
                            if path_str and path_str in full_answer:
                                if is_success:
                                    store.mark_helpful(leaf.node_id)
                                else:
                                    store.mark_not_helpful(leaf.node_id)
                except Exception as e:
                    logger.debug("反馈回写跳过: %s", e)

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
