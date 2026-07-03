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
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.native.types import (
    AgentLoopState,
    AgentResult,
    Attachment,
    ExecutionPlan,
    RunContext,
)
from floodmind.agent.runtime.contracts.subagent import SubAgentReport
from floodmind.agent.runtime.contracts.tools import ToolSpec
from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.agent.native.artifact_watcher import ArtifactWatcher
from floodmind.agent.native.context_compressor import ContextCompressor
from floodmind.agent.native.event_bus import EventBus, StepEventBus
from floodmind.agent.native.executor import NativeAgentExecutor
from floodmind.agent.native.message_builder import MessageBuilder
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.native.tool_runtime import native_from_agent_tool

from floodmind.config.settings import settings
from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
from floodmind.agent.runtime.services.execution_journal_service import ExecutionJournalService
from floodmind.agent.runtime.services.sandbox_service import SandboxService
from floodmind.agent.runtime.services.tracing_service import TracingService
from floodmind.agent.runtime.services.workspace_service import get_workspace
from floodmind.tools.session_context import get_current_session_output_dir

logger = logging.getLogger(__name__)

# 子任务 JSON Schema（create_plan / update_plan 共享）
_SUBTASK_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "content": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
            "priority": {"type": "string", "enum": ["high", "normal", "low"]},
        },
    },
    "description": "本步骤下的细粒度子任务（可选）",
}

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
    # msg[0] STATIC_GLOBAL: 身份(SOUL.md)/行为指导/Skill+工具目录 — 跨 session 稳定
    # msg[1] STATIC_PER_PROJECT: AGENTS.md 项目/全局规则 — 仅 AGENTS.md 变更时变化
    # msg[2] STATIC_PER_SESSION: 系统时间 + 会话路径/环境 — 每个 session 不同
    #
    # 顺序：STATIC_GLOBAL → STATIC_PER_PROJECT → STATIC_PER_SESSION

    # 保持向后兼容的旧模板（仅用于 agent_info.prompt override 路径）
    SYSTEM_PROMPT_STATIC_GLOBAL = None

    SYSTEM_PROMPT_PROJECT_TEMPLATE = """{project_context}"""

    SYSTEM_PROMPT_SESSION_TEMPLATE = """## 当前系统时间
{current_time_context}

## 当前会话信息
{session_env}"""

    @classmethod
    def _build_stable_prompt(cls, skill_catalog: str, tool_descriptions: str, tool_registry=None) -> str:
        """组装稳定层 (STATIC_GLOBAL) 提示词。

        分层结构：
        1. 身份层 — SOUL.md 或 DEFAULT_FLOODMIND_IDENTITY
        2. 行为指导层 — 工作方式/工具使用/工作流等
        3. Skill + 工具目录 — 运行时动态
        """
        from floodmind.profile.soul import load_soul_md, DEFAULT_FLOODMIND_IDENTITY
        from floodmind.profile.guidance import (
            WORK_METHOD_GUIDANCE,
            SCHEDULED_TASK_GUIDANCE,
            PREFERENCE_GUIDANCE,
            TOOL_EXECUTION_GUIDANCE,
            WORKFLOW_GUIDANCE,
            ARTIFACT_JUDGMENT_GUIDANCE,
            OUTPUT_FORMAT_GUIDANCE,
            MEMORY_GUIDANCE,
        )

        soul = load_soul_md() or DEFAULT_FLOODMIND_IDENTITY

        tool_names = set()
        if tool_registry:
            tool_names = {t.name for t in tool_registry.all()}

        conditional_guidance = []
        if any(n in tool_names for n in ("CreateScheduledTask", "create_scheduled_task")):
            conditional_guidance.append(SCHEDULED_TASK_GUIDANCE)
        if any(n in tool_names for n in ("UpdateProjectInstructions",)):
            conditional_guidance.append(PREFERENCE_GUIDANCE)

        guidance_parts = (
            [WORK_METHOD_GUIDANCE]
            + conditional_guidance
            + [
                TOOL_EXECUTION_GUIDANCE,
                MEMORY_GUIDANCE,
                WORKFLOW_GUIDANCE,
                ARTIFACT_JUDGMENT_GUIDANCE,
                OUTPUT_FORMAT_GUIDANCE,
            ]
        )

        parts = [
            soul,
            "\n\n".join(guidance_parts),
            f"## 可用 skills\n{skill_catalog}",
            f"## 可用工具\n{tool_descriptions}",
        ]
        return "\n\n".join(parts)


    SPECIALIST_STATIC_GLOBAL = """你是 FloodMind 子代理，负责完成主代理分配的独立子任务。

## 你的职责
1. 执行主代理分配的子任务
2. 根据需要运行 skill 脚本
3. 编写并执行临时 Python 脚本

## 执行原则
- 主动从原始文件、工具结果获取真实信息，使任务结果充实准确
- 专注于当前分配的任务，完成后立即返回结果
- 如果指令缺文件、缺参数、缺前置产物，明确指出缺什么
- 使用 skill 前先调用 `GetSkill` 查看详细说明，再决定下一步

## 工具使用
- 调用工具时一次只传一个参数
- Bash 可执行任何 shell 命令（python、node、npm 等运行时）
- skill 指定非 Python 技术栈时，用 Write 写脚本文件，再用 Bash 执行
- 所有路径参数使用绝对路径
- 超长数据用文件中转，工具参数保持精简
- 大数组从原始文件读取

## skill 与产物准确性
- 所有脚本、参数、字段以 skill 说明或文件实际内容为准
- 目标达成即返回结果

## 可使用 skills
{skill_catalog}

## 输出要求
- 简洁说明本次任务是否完成
- 返回直接结果：生成文件路径、读取/搜索结果、关键输出摘要
"""

    SPECIALIST_PROJECT_TEMPLATE = """{project_context}"""

    SPECIALIST_SESSION_TEMPLATE = """## 当前会话信息
{session_env}"""

    def __init__(
        self,
        llm_service=None,
        memory=None,
        session_id: str = "",
        enable_search: bool = False,
        enable_reasoning: bool = False,
        agent_type: str = "build",
        bare: bool = False,
        tools: Optional[list] = None,
        system_prompt: Optional[str] = None,
        tracing_service: Optional[TracingService] = None,
        max_iterations: int = 10000,
        permission_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        **kwargs,
    ):
        self.llm_service = llm_service
        self.memory = memory
        self.session_id = session_id
        self._enable_search = enable_search
        self._enable_reasoning = enable_reasoning
        self._agent_type = agent_type
        self._bare = bare

        from floodmind.agent.agent_registry import get_agent
        self._agent_info = get_agent(agent_type) or get_agent("build")

        from floodmind.profile.soul import seed_default_soul
        seed_default_soul()

        self._orchestrator_registry = _InstanceToolRegistry()
        self._specialist_registry = _InstanceToolRegistry()
        self._event_bus = EventBus()
        self._message_builder = MessageBuilder()

        self._model_client: Optional[ModelClient] = None
        self._orchestrator_executor: Optional[NativeAgentExecutor] = None
        self._specialist_executor: Optional[NativeAgentExecutor] = None
        self._tool_executor: Optional[Any] = None
        self._artifact_watcher: Optional[ArtifactWatcher] = None
        self._artifact_lock = threading.Lock()
        self._plan_lock = threading.Lock()

        self._skill_catalog = ""
        self._active_user_message = ""
        self._step_start_time = 0.0
        self._last_loop_state: Optional[AgentLoopState] = None
        self._current_run_context: Optional[RunContext] = None
        self._orchestrator_extra_body: dict = {}

        self._cached_experience_context: str = ""
        self._cached_experience_version: int = -1
        self._tracing_service = tracing_service or TracingService()
        # SDK 可配置项（bare 模式由 _init_bare 消费）
        self._max_iterations = max_iterations
        self._permission_handler = permission_handler
        self._sandbox_service = SandboxService()
        self._checkpoint_service = CheckpointService(tracing_service=self._tracing_service)
        self._journal_service = ExecutionJournalService()

        # ── bare 模式：精简初始化（嵌入 SDK 用） ──
        if bare:
            self._init_bare(tools or [], system_prompt)
            logger.info("NativeFloodAgent (bare) 初始化成功")
            return

        # ── 完整模式（原有路径） ──
        self._init_tools()
        self._init_model_client()
        self._init_executors()
        if settings.agent.enable_chronos_warmup:
            self._warmup_chronos()

        logger.info("NativeFloodAgent 初始化成功")

    _chronos_warmup_done = False
    _chronos_warmup_lock = threading.Lock()

    def _init_bare(self, tools: list, system_prompt: Optional[str]) -> None:
        """bare 模式精简初始化：只注册用户提供的工具，跳过内置工具/权限/MCP/Plugin。"""
        # 注册用户工具（支持 AgentTool 和 ToolSpec 两种格式）
        self._orchestrator_registry.register_tools(tools)
        self._specialist_registry.register_tools(tools)

        # 构建 skill catalog（bare 模式下通常为空）
        self._skill_catalog = ""

        # 工具描述
        tool_descriptions = self._build_tool_descriptions(self._orchestrator_registry)

        # 提示词：用户自定义 or 最小默认
        prompt = system_prompt or "你是一个智能助手，使用可用工具帮助用户完成任务。"

        # 初始化 model client：如果传入的已是 ModelClient 实例则直接复用
        if isinstance(self.llm_service, ModelClient):
            self._model_client = self.llm_service
            if self.llm_service.enable_thinking:
                self._orchestrator_extra_body = {"enable_thinking": True}
        else:
            self._init_model_client()

        # 初始化 tool executor（bare 模式：默认允许所有调用；可选 permission_handler 钩子）
        from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService
        self._tool_executor = ToolExecutionService(
            tracing_service=self._tracing_service,
            permission_handler=self._permission_handler,
        )

        context_compressor = self._make_context_compressor()
        context_window = settings.agent.context_window

        # 构建 executor
        self._orchestrator_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=self._max_iterations,
            extra_body=self._orchestrator_extra_body,
            system_prompts=[prompt + "\n\n## 可用工具\n" + tool_descriptions],
            tools_schema=self._orchestrator_registry.tools_schema(),
            tool_registry=self._orchestrator_registry,
            checkpoint_service=self._checkpoint_service,
            execution_journal_service=self._journal_service,
            tracing_service=self._tracing_service,
            context_compressor=context_compressor,
            context_window=context_window,
            memory=self.memory,
        )

        self._specialist_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=self._max_iterations,
            system_prompts=[prompt],
            tools_schema=self._specialist_registry.tools_schema(),
            tool_registry=self._specialist_registry,
            checkpoint_service=self._checkpoint_service,
            execution_journal_service=self._journal_service,
            tracing_service=self._tracing_service,
            context_compressor=context_compressor,
            context_window=context_window,
        )

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
            web_search, fetch_webpage, add_memory, search_memory,
            update_project_instructions,
            create_scheduled_task, list_scheduled_tasks, cancel_scheduled_task,
            set_memory_instance, reset_retry_guard,
        )
        from floodmind.tools.memory_tools import (
            conversation_search,
            experience_search,
            core_memory_append,
            core_memory_read,
            journal_search,
            journal_get_full_result,
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

        # 子代理沙盒规则：禁止级联委派、默认禁止网络
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_nested",
            tool_name="SubAgent",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理内禁止再启动子代理",
        ))
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_parallel",
            tool_name="ParallelTask",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理内禁止并行委派",
        ))
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_network",
            tool_name="WebSearch",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理默认禁止网络搜索",
        ))
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_fetch",
            tool_name="WebFetch",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理默认禁止网页抓取",
        ))
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_mcp_load",
            tool_name="LoadMcpServer",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理禁止动态加载外部 MCP Server",
        ))
        perm_svc.add_deny_rule(PermissionRule(
            name="subagent_no_project_instructions",
            tool_name="UpdateProjectInstructions",
            session_id_pattern=r"^sub-",
            behavior=PermissionBehavior.DENY,
            reason="子代理禁止修改项目级指令",
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
            conversation_search,
            experience_search,
            core_memory_append,
            core_memory_read,
            journal_search,
            journal_get_full_result,
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
        # 子代理工具白名单：排除依赖主代理 memory 实例的工具（子代理 clean slate 无 memory，
        # 调用会报"记忆系统未初始化"）与子代理禁止的编排工具（已有 permission deny 规则，
        # 从工具表移除可避免 LLM 误调用浪费一轮）。对齐 Claude Code 子代理 tools 白名单设计。
        _SPECIALIST_EXCLUDED_TOOLS = {
            "MemorySearch",               # 依赖主代理 memory 实例
            "ConversationSearch",         # 同上
            "UpdateProjectInstructions",  # 子代理禁止修改项目指令
        }
        specialist_tools = [
            t for t in all_tools
            if getattr(t, "name", "") not in _SPECIALIST_EXCLUDED_TOOLS
        ]
        self._specialist_registry.register_tools(specialist_tools)

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
                        "description": "执行步骤JSON数组，每个元素含title、executor、skill_name(可选)、purpose、expected_deliverables(JSON数组)、subtasks(可选)",
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
                                "subtasks": _SUBTASK_SCHEMA,
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
            name="update_plan",
            description=(
                "动态调整执行计划。action=add_step 时传 step（含 step_id/title/purpose/needs/subtasks 等）；"
                "action=update_step 时传 step_id + 可选 status(pending/running/completed/error/skipped)/output_summary/output_artifacts/subtasks；"
                "action=remove_step 时传 step_id。执行中发现规划不足（缺步骤/某步无需再做/需要拆分）、或自己完成某步想明确标记状态时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add_step", "update_step", "remove_step"], "description": "操作类型"},
                    "step": {
                        "type": "object",
                        "description": "add_step 时传入完整步骤定义（含 step_id/title/purpose/needs/subtasks 等）",
                        "properties": {
                            "step_id": {"type": "string"},
                            "title": {"type": "string"},
                            "purpose": {"type": "string"},
                            "skill_name": {"type": "string"},
                            "executor": {"type": "string"},
                            "needs": {"type": "array", "items": {"type": "string"}},
                            "expected_deliverables": {"type": "array", "items": {"type": "object"}},
                            "subtasks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "content": {"type": "string"},
                                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                                        "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                                    },
                                },
                                "description": "本步骤下的细粒度子任务（可选）",
                            },
                        },
                    },
                    "step_id": {"type": "string", "description": "update_step / remove_step 时传入目标步骤ID"},
                    "status": {"type": "string", "enum": ["pending", "running", "completed", "error", "skipped"]},
                    "output_summary": {"type": "string", "description": "该步骤的产出摘要"},
                    "output_artifacts": {"type": "array", "items": {"type": "string"}, "description": "该步骤产出的文件路径"},
                    "subtasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                                "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                            },
                        },
                        "description": "update_step 时覆盖该步骤下的子任务列表",
                    },
                },
                "required": ["action"],
            },
            func=self._handle_update_plan,
            is_readonly=False,
            is_destructive=False,
            is_concurrency_safe=True,
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
        ))

        # 阶段E：exit_plan_mode（仅主代理，policy="ask"，提交计划等用户审批）
        self._orchestrator_registry.register(ToolSpec(
            name="exit_plan_mode",
            description="提交最终执行计划并请求用户审批。规划模式下调用此工具，将计划呈现给用户确认。获批后进入执行模式，解禁写/执行/委派工具。参数 plan_summary 应包含计划摘要。",
            parameters={
                "type": "object",
                "properties": {
                    "plan_summary": {"type": "string", "description": "执行计划的最终摘要，用户将审核此内容后决定批准或拒绝"},
                },
                "required": ["plan_summary"],
            },
            func=self._handle_exit_plan_mode,
            is_readonly=True,
            is_destructive=False,
            is_concurrency_safe=True,
            permission_policy=ToolPermissionPolicy(policy_type="ask", reason="提交执行计划需用户审批"),
        ))

        self._orchestrator_registry.register(ToolSpec(
            name="SubAgent",
            description="启动子代理辅助完成子任务。适用于：耗时脚本运行、独立子任务、需要并行处理的搜索等。注意：需要丰富上下文的写作/报告任务应自己做，不要委派。若已明确要复用某个 skill，同时传入 skill_name。可选 workdir 指定子代理工作目录（桌面版用于并行写不同子目录）；不传则子代理在独立 sandbox 内工作。",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "交给子代理的明确任务说明，应尽量具体、可执行"},
                    "skill_name": {"type": "string", "description": "若当前任务明确要求复用某个 skill，则传入对应的 skill 名称"},
                    "workdir": {"type": "string", "description": "可选。子代理工作目录（绝对路径或相对当前工作区）。指定后子代理在该目录下读写，未指定则用独立 sandbox。并行任务指定不同 workdir 可避免产物互相覆盖"},
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
                        "description": "可并行执行的任务列表，每个元素含 task(任务说明)、skill_name(可选)、step_key(可选，对应 create_plan 中的步骤ID)、workdir(可选，子代理工作目录)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "交给子代理的明确任务说明"},
                                "skill_name": {"type": "string", "description": "若任务明确要求复用某个 skill，传入 skill 名称"},
                                "step_key": {"type": "string", "description": "对应执行计划中的步骤ID，如 step-1"},
                                "workdir": {"type": "string", "description": "可选。子代理工作目录。指定后子代理在该目录下读写，未指定则用独立 sandbox。并行任务指定不同 workdir 可避免产物互相覆盖"},
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
            tracing_service=self._tracing_service,
        )

        # ── Plugin 系统集成 ──
        self._load_plugins()

    def _load_plugins(self) -> None:
        """加载并注册 Plugin 提供的工具和 hooks。"""
        try:
            from floodmind.plugin import PluginLoader
            loader = PluginLoader()
            plugins = loader.discover()
            if not plugins:
                return
            for plugin in plugins:
                # 注册工具
                for tool in plugin.get_tools():
                    self._orchestrator_registry.register(tool)
                    logger.info("Plugin '%s' registered tool: %s", plugin.name, tool.name)
                # 注册 hooks
                for event_type, handler in plugin.get_hooks().items():
                    self._event_bus.add_listener(
                        lambda e, et=event_type, h=handler: h(e) if e.get("type") == et else None
                    )
                # Agent 初始化回调
                try:
                    plugin.on_agent_init(self)
                except Exception as e:
                    logger.warning("Plugin '%s' on_agent_init error: %s", plugin.name, e)
            logger.info("Loaded %d plugin(s)", len(plugins))
        except Exception as e:
            logger.warning("Plugin loading failed (non-fatal): %s", e)

    def _make_context_compressor(self) -> Optional[ContextCompressor]:
        """创建上下文压缩器，供 orchestrator executor 使用。"""
        if self._model_client is None:
            return None
        return ContextCompressor(
            model_client=self._model_client,
            head_keep=2,
            tail_keep=4,
            trigger_threshold=0.75,
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
        # Windows 用 Windows PowerShell 5.1（不支持 `&&`），明确告知 agent，避免命令反复试错浪费轮次
        shell_hint = (
            "Shell 注意: Windows PowerShell 不支持 `&&` 连接命令，多命令请用 `;` 分隔，或写入 .ps1/.py 文件执行；"
            "多行 Python 必须先写入 .py 文件再 `python xxx.py`，禁止 `python -c` 多行（极易触发语法错误）。\n"
            if os.name == "nt" else ""
        )
        session_env = (
            f"会话输出目录: {output_dir}\n"
            f"上传目录: {upload_dir}\n"
            f"操作系统: {os_name}\n"
            f"Shell: {shell_name}\n"
            f"{shell_hint}"
            f"路径风格: {os_name}"
        )

        self._project_context = project_context
        self._current_time_context = current_time_context
        self._session_env = session_env

        tool_descriptions = self._build_tool_descriptions(self._orchestrator_registry)

        # Use agent-specific prompt if defined, otherwise split into three parts for cache reuse.
        agent_prompt = getattr(self._agent_info, "prompt", "") if self._agent_info else ""
        if agent_prompt:
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
            orchestrator_prompts = [
                self._build_stable_prompt(
                    skill_catalog=self._skill_catalog,
                    tool_descriptions=tool_descriptions,
                    tool_registry=self._orchestrator_registry,
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

        context_compressor = self._make_context_compressor()
        context_window = settings.agent.context_window

        self._orchestrator_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=10000,
            extra_body=self._orchestrator_extra_body,
            system_prompts=orchestrator_prompts,
            tools_schema=self._orchestrator_registry.tools_schema(),
            tool_registry=self._orchestrator_registry,
            checkpoint_service=self._checkpoint_service,
            execution_journal_service=self._journal_service,
            tracing_service=self._tracing_service,
            context_compressor=context_compressor,
            context_window=context_window,
            memory=self.memory,
        )

        self._specialist_executor = NativeAgentExecutor(
            model_client=self._model_client,
            tool_executor=self._tool_executor,
            event_bus=self._event_bus,
            message_builder=self._message_builder,
            max_iterations=10000,
            system_prompts=specialist_prompts,
            tools_schema=self._specialist_registry.tools_schema(),
            tool_registry=self._specialist_registry,
            checkpoint_service=self._checkpoint_service,
            execution_journal_service=self._journal_service,
            tracing_service=self._tracing_service,
            context_compressor=context_compressor,
            context_window=context_window,
        )

    def _register_mcp_tools(self, server_name: str, conn, registry) -> int:
        """将 MCP Server 的工具注册到指定 registry，返回注册数量"""
        # MCP 工具调用闭包：捕获 server/tool 名藏入函数体（不暴露为参数，避免与工具
        # 自身参数名碰撞）；参数走 **kwargs，与内置工具经 tool.func(**validated_args)
        # 调用的协议完全一致。
        def _make_mcp_func(sn: str, tn: str):
            full_name = f"mcp:{sn}:{tn}"
            def _func(**kwargs):
                return self._mcp_pool.call_tool(full_name, kwargs) if self._mcp_pool else "MCP 连接已断开"
            return _func

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
                func=_make_mcp_func(server_name, mt.get('name', '')),
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

        agent_prompt = getattr(self._agent_info, "prompt", "") if self._agent_info else ""
        if agent_prompt:
            ctc = getattr(self, "_current_time_context", "")
            se = getattr(self, "_session_env", "")
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
            new_global = self._build_stable_prompt(
                skill_catalog=self._skill_catalog,
                tool_descriptions=tool_descriptions,
                tool_registry=self._orchestrator_registry,
            )
            new_project = self.SYSTEM_PROMPT_PROJECT_TEMPLATE.format(
                project_context=pc,
            )
            prompts = list(self._orchestrator_executor.system_prompts)
            prompts[0] = new_global
            if len(prompts) > 1:
                prompts[1] = new_project
            self._orchestrator_executor.system_prompts = prompts

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

    def _normalize_plan_step(self, raw: Any, fallback_index: int) -> Dict[str, Any]:
        """把一个原始步骤定义归一化为标准的 plan step dict。"""
        if not isinstance(raw, dict):
            raw = {"title": str(raw)[:60]}
        step_id = raw.get("step_id") or f"step-{fallback_index + 1}"
        expected = raw.get("expected_deliverables", [])
        if isinstance(expected, str):
            try:
                expected = json.loads(expected)
            except Exception:
                expected = [{"type": expected}]
        if not isinstance(expected, list):
            expected = [expected] if expected else []

        subtasks = raw.get("subtasks", []) or []
        if not isinstance(subtasks, list):
            subtasks = []
        normalized_subtasks = []
        for idx, st in enumerate(subtasks):
            if not isinstance(st, dict):
                continue
            normalized_subtasks.append({
                "id": str(st.get("id") or f"{step_id}-sub-{idx + 1}"),
                "content": str(st.get("content", "") or ""),
                "status": str(st.get("status", "") or "pending"),
                "priority": str(st.get("priority", "") or "normal"),
            })

        return {
            "step_id": step_id,
            "title": str(raw.get("title", "") or f"步骤 {fallback_index + 1}"),
            "executor": str(raw.get("executor", "") or "execution_specialist"),
            "skill_name": str(raw.get("skill_name", "") or ""),
            "purpose": str(raw.get("purpose", "") or ""),
            "status": str(raw.get("status", "") or "pending"),
            "needs": raw.get("needs", []) or [],
            "expected_deliverables": expected,
            "output_artifacts": raw.get("output_artifacts", []) or [],
            "output_summary": str(raw.get("output_summary", "") or ""),
            "error_message": str(raw.get("error_message", "") or ""),
            "attempt_count": int(raw.get("attempt_count", 0) or 0),
            "subtasks": normalized_subtasks,
        }

    def _normalize_artifacts(self, art: Any) -> List[str]:
        """把 artifacts 参数归一化为 list[str]。"""
        if isinstance(art, str):
            try:
                art = json.loads(art)
            except json.JSONDecodeError:
                art = [art] if art else []
        if isinstance(art, list):
            return [str(a) for a in art if a]
        if art:
            return [str(art)]
        return []

    def _emit_plan_full(self, plan: ExecutionPlan, title: str = "") -> None:
        """全量下发执行计划到前端 + 记录 trace。"""
        self._event_bus.emit_workflow_plan(
            title=title or plan.user_message,
            steps=[
                {
                    "key": s["step_id"],
                    "label": s["title"],
                    "title": s["title"],
                    "status": s["status"],
                    "detail": s["purpose"],
                    "outcome": s.get("output_summary", ""),
                    "expected_deliverables": s.get("expected_deliverables", []),
                    "output_artifacts": s.get("output_artifacts", []),
                    "subtasks": s.get("subtasks", []),
                }
                for s in plan.steps
            ],
        )
        session_id = self.session_id or (self._current_run_context.session_id if self._current_run_context else "")
        if self._tracing_service is not None:
            self._tracing_service.record_event(
                session_id,
                "workflow",
                "workflow_plan",
                input={"title": title or plan.user_message, "steps": plan.steps},
            )

    def _handle_create_plan(self, user_goal: str = "", deliverables: str = "", steps: Any = "") -> str:
        steps_str = json.dumps(steps, ensure_ascii=False) if isinstance(steps, (list, dict)) else str(steps)
        try:
            parsed_steps = json.loads(steps_str) if steps_str else []
        except json.JSONDecodeError:
            parsed_steps = [{"title": steps_str[:60] if steps_str else "执行任务", "executor": "execution_specialist", "purpose": user_goal, "expected_deliverables": []}]

        if not isinstance(parsed_steps, list):
            parsed_steps = [parsed_steps]

        normalized_steps = [self._normalize_plan_step(raw, i) for i, raw in enumerate(parsed_steps)]

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

        self._emit_plan_full(plan, title=user_goal)

        summary = f"执行计划已创建: {len(normalized_steps)} 个步骤, 交付物: {deliverables or '无特定类型'}"
        logger.info("[create_plan] %s", summary)
        return summary

    def _handle_update_plan(
        self,
        action: str = "",
        step: Any = "",
        step_id: str = "",
        status: str = "",
        output_summary: str = "",
        output_artifacts: Any = "",
        subtasks: Any = "",
    ) -> str:
        """动态调整执行计划：add_step / update_step / remove_step。"""
        plan = self._last_loop_state.plan if self._last_loop_state else None
        if plan is None:
            return "错误：当前没有执行计划，请先用 create_plan 创建"

        action = str(action).strip().lower()

        if action == "add_step":
            if not step:
                return "错误：add_step 需要传入 step（含 step_id/title/purpose 等）"
            new_step = self._normalize_plan_step(step, fallback_index=len(plan.steps))
            if plan.find_step(new_step["step_id"]):
                return f"错误：步骤 {new_step['step_id']} 已存在"
            plan.steps.append(new_step)
            if plan.has_cycle():
                plan.steps.pop()
                return f"错误：新增步骤 {new_step['step_id']} 会造成依赖环，已回滚"

        elif action == "update_step":
            step_id = str(step_id).strip()
            target = plan.find_step(step_id)
            if target is None:
                return f"错误：步骤 {step_id} 不存在"
            if status in ("pending", "running", "completed", "error", "skipped"):
                target["status"] = status
            if step and isinstance(step, dict):
                if step.get("title"):
                    target["title"] = str(step["title"])
                if "purpose" in step:
                    target["purpose"] = str(step["purpose"])
                if "skill_name" in step:
                    target["skill_name"] = str(step["skill_name"])
                if "executor" in step:
                    target["executor"] = str(step["executor"])
                if "expected_deliverables" in step:
                    target["expected_deliverables"] = step["expected_deliverables"]
            if output_summary:
                target["output_summary"] = str(output_summary)[:500]
            if output_artifacts:
                target["output_artifacts"] = self._normalize_artifacts(output_artifacts)
            if subtasks:
                normalized = self._normalize_plan_step(
                    {"step_id": step_id, "subtasks": subtasks}, fallback_index=0
                )
                target["subtasks"] = normalized.get("subtasks", [])

            # 发送单步 delta，让前端无需等待完整计划快照即可更新
            self._event_bus.emit_workflow_step(
                step_key=step_id,
                status=target.get("status", "pending"),
                title=target.get("title", ""),
                detail=target.get("purpose", ""),
                outcome=target.get("output_summary", ""),
                subtasks=target.get("subtasks", []),
            )

        elif action == "remove_step":
            step_id = str(step_id).strip()
            before = len(plan.steps)
            # 检查是否有其他步骤依赖该步骤
            dependents = [s.get("step_id") for s in plan.steps if step_id in (s.get("needs") or [])]
            if dependents:
                return f"错误：步骤 {step_id} 仍被 {', '.join(dependents)} 依赖，无法删除"
            plan.steps = [s for s in plan.steps if s.get("step_id") != step_id]
            if len(plan.steps) == before:
                return f"错误：步骤 {step_id} 不存在"

        else:
            return "错误：action 仅支持 add_step / update_step / remove_step"

        plan.updated_at = datetime.now().isoformat()
        self._emit_plan_full(plan)
        return f"计划已更新（action={action}），当前共 {len(plan.steps)} 步"

    # ── 阶段E：exit_plan_mode（提交计划等审批，翻 mode） ──
    def _handle_exit_plan_mode(self, plan_summary: str = "") -> str:
        """提交执行计划。由 permission_policy="ask" 驱动 AskService 审批。

        用户批准后 executor 恢复 → 此 handler 翻 state.mode="execution" → 写/委派解禁。
        """
        if not plan_summary.strip():
            return "错误：plan_summary 不能为空，请提供执行计划摘要供用户审批"

        # 翻 mode：用户已批准计划，进入执行模式
        if self._last_loop_state is not None:
            self._last_loop_state.mode = "execution"
            logger.info("[MODE] planning → execution (plan approved by user)")

        # 发送计划审批事件
        self._event_bus.emit_tool_status(
            "exit_plan_mode", "completed",
            tool_input=json.dumps({"plan_summary": plan_summary[:200]}, ensure_ascii=False),
        )

        return f"计划已批准，进入执行模式。计划摘要:\n{plan_summary}"

    def _run_specialist_task(
        self,
        task_text: str,
        skill_name: str,
        parent_context: RunContext,
        step_key: str,
        step_event_bus: Optional[EventBus] = None,
        delegate_cwd: Optional[str] = None,
    ) -> SubAgentReport:
        """在独立 session 中运行一个 specialist 子代理，并保存独立 checkpoint。

        子代理有自己的 session_id、AgentLoopState、checkpoint 和沙盒工作区。
        delegate_cwd（阶段C）：主代理委派时指定子代理工作目录。指定则子代理默认 cwd =
        delegate_cwd（桌面版直接在 user_dir 子目录干活，无需回流）；未指定走 sandbox。
        """
        import uuid
        from floodmind.agent.native.artifact_watcher import ArtifactWatcher

        sub_session_id = f"sub-{parent_context.session_id}-{step_key}-{uuid.uuid4().hex[:8]}"
        specialist_input = self._build_specialist_user_input(task_text, skill_name)

        sandbox_ctx = self._sandbox_service.create(
            sub_session_id=sub_session_id,
            parent_output_dir=Path(parent_context.output_dir) if parent_context.output_dir else None,
            delegate_cwd=Path(delegate_cwd) if delegate_cwd else None,
        )

        # 子代理默认 cwd：delegate_cwd 优先，否则 sandbox outputs
        sub_cwd = str(sandbox_ctx.delegate_cwd) if sandbox_ctx.delegate_cwd else str(sandbox_ctx.outputs_dir)

        try:
            sub_context = RunContext(
                session_id=sub_session_id,
                user_text=specialist_input,
                attachments=list(parent_context.attachments),
                output_dir=str(sandbox_ctx.outputs_dir),
                upload_dir=str(sandbox_ctx.uploads_dir),
                enable_reasoning=parent_context.enable_reasoning,
                abort_check=parent_context.abort_check,
                delegate_cwd=sub_cwd,
                agent_tier="sub",
            )

            sub_state = AgentLoopState(
                session_id=sub_session_id,
                run_id=f"run-{int(time.time())}",
                status="created",
                user_message=specialist_input,
                original_input=specialist_input,
            )
            sub_state.messages = self._specialist_executor._build_initial_messages(
                context=sub_context,
                user_text=specialist_input,
                attachments=list(parent_context.attachments),
                memory_messages=[],
            )

            # 包装 event_bus：确保子代理事件带 _trace_session，写入子代理自己的 trace
            base_bus = step_event_bus or self._event_bus
            if isinstance(base_bus, StepEventBus) and not getattr(base_bus, "_trace_session_id", ""):
                # 并行路径传入的 StepEventBus 没有 trace_session_id，补上
                base_bus._trace_session_id = sub_session_id
                event_bus = base_bus
            elif isinstance(base_bus, StepEventBus):
                event_bus = base_bus
            else:
                # 串行委派：用父 EventBus 包装一层带 trace_session 的 StepEventBus
                event_bus = StepEventBus(base_bus, step_key, trace_session_id=sub_session_id)
            specialist_executor = NativeAgentExecutor(
                model_client=self._model_client,
                tool_executor=self._tool_executor,
                event_bus=event_bus,
                message_builder=MessageBuilder(),
                max_iterations=10000,
                system_prompts=list(self._specialist_executor.system_prompts),
                tools_schema=self._specialist_registry.tools_schema(),
                tool_registry=self._specialist_registry,
                checkpoint_service=self._checkpoint_service,
                execution_journal_service=self._journal_service,
                tracing_service=self._tracing_service,
            )

            result = specialist_executor.run_from_state(context=sub_context, state=sub_state)

            # 产物检测（基于子代理自己的 workspace）
            watcher = ArtifactWatcher(output_dir=sub_context.output_dir, upload_dir=sub_context.upload_dir)
            watcher.take_snapshot()
            workspace_artifacts = [a.file_path for a in watcher.detect_new_artifacts()]

            # 回流到父 output_dir，并更新 artifacts 路径
            artifacts = self._sandbox_service.copy_artifacts_to_parent(
                sandbox_ctx,
                workspace_artifacts,
            )

            has_tool_success = any(tr.status == "completed" for tr in result.tool_results) if result.tool_results else False
            completed = bool(result.final_output or has_tool_success or artifacts)

            tool_summaries = []
            for tr in result.tool_results:
                tool_summaries.append({
                    "tool_name": tr.name,
                    "status": tr.status,
                    "summary": (tr.content[:200] + "...") if len(tr.content) > 200 else tr.content,
                })

            return SubAgentReport(
                summary=result.final_output or "",
                completed=completed,
                outputs={},
                artifacts=artifacts,
                next_steps=[],
                needs_human=False,
                sub_session_id=sub_session_id,
                tool_result_summaries=tool_summaries,
            )
        finally:
            self._sandbox_service.destroy(sandbox_ctx)

    def _handle_delegate_specialist(self, task: str = "", skill_name: str = "", workdir: str = "") -> str:
        task = (task or "").strip()
        skill_name = (skill_name or "").strip()
        workdir = (workdir or "").strip()
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
        self._emit_workflow_step_progress(
            step_key=step_key,
            status="running",
            title=task[:60] if task else "委派执行",
            detail=skill_name or "",
        )

        self._step_start_time = time.time()
        with self._artifact_lock:
            if self._artifact_watcher:
                self._artifact_watcher.take_snapshot()

        sub_report = self._run_specialist_task(
            task_text=task,
            skill_name=skill_name,
            parent_context=context,
            step_key=step_key,
            delegate_cwd=workdir or None,
        )

        step_status = "completed" if sub_report.completed else "error"
        output = sub_report.summary
        artifacts = sub_report.artifacts

        if self._last_loop_state is not None and self._last_loop_state.plan is not None:
            plan_step = self._last_loop_state.plan.find_step(step_key)
            if plan_step:
                plan_step["status"] = step_status
        self._emit_workflow_step_progress(
            step_key=step_key,
            status=step_status,
            title=task[:60] if task else "委派执行",
            outcome=output[:200] if output else "",
        )

        payload = {
            "stage": "execution_specialist",
            "stage_label": "Execution Specialist",
            "result_type": "intermediate",
            "status": "completed" if step_status == "completed" else "error",
            "user_goal": _active_input_var.get(),
            "task": task,
            "skill_name": skill_name,
            "summary": output,
            "artifacts": artifacts,
            "sub_session_id": sub_report.sub_session_id,
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
            workdir = (task_def.get("workdir") or "").strip()
            step_key = task_def.get("step_key") or f"parallel-{idx}"

            if not task_text:
                return (step_key, {"status": "error", "summary": "task 为空", "artifacts": []})

            self._emit_workflow_step_progress(
                step_key=step_key,
                status="running",
                title=task_text[:60],
                detail=skill_name or "",
            )

            step_event_bus = StepEventBus(self._event_bus, step_key)

            try:
                sub_report = self._run_specialist_task(
                    task_text=task_text,
                    skill_name=skill_name,
                    parent_context=context,
                    step_key=step_key,
                    step_event_bus=step_event_bus,
                    delegate_cwd=workdir or None,
                )
            except Exception as e:
                logger.error("[并行委派] 任务 %s 执行异常: %s", step_key, e)
                self._emit_workflow_step_progress(
                    step_key=step_key,
                    status="error",
                    title=task_text[:60],
                    outcome=str(e)[:200],
                )
                return (step_key, {"status": "error", "summary": str(e)[:200], "artifacts": []})

            step_status = "completed" if sub_report.completed else "error"
            output = sub_report.summary
            artifacts = sub_report.artifacts

            if self._last_loop_state is not None and self._last_loop_state.plan is not None:
                with self._plan_lock:
                    plan_step = self._last_loop_state.plan.find_step(step_key)
                    if plan_step:
                        plan_step["status"] = step_status

            self._emit_workflow_step_progress(
                step_key=step_key,
                status=step_status,
                title=task_text[:60],
                outcome=output[:200] if output else "",
            )

            return (step_key, {
                "status": step_status,
                "summary": output[:500] if output else "",
                "artifacts": artifacts,
                "sub_session_id": sub_report.sub_session_id,
            })

        with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            futures = {}
            for i, task_def in enumerate(tasks):
                future = pool.submit(_run_single, i, task_def)
                futures[future] = i

            pending = set(futures)
            while pending:
                # 暂停/中断感知：每 0.5s 醒来检查 abort，立即取消未完成 future 并标记 interrupted。
                # 解决主代理阻塞在 awaiting_tool（future.result 300s）无法响应暂停的根因。
                abort_check = getattr(context, "abort_check", None)
                if abort_check and abort_check():
                    logger.info("[并行委派] 检测到中断，取消剩余 %d 个任务", len(pending))
                    for f in pending:
                        f.cancel()
                    for f in pending:
                        step_key = f"parallel-{futures[f]}"
                        if step_key not in results:
                            results[step_key] = {
                                "status": "interrupted",
                                "summary": "任务因用户中断而中止",
                                "artifacts": [],
                            }
                    break
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
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
                workdir = (task_def.get("workdir") or "").strip()

                self._emit_workflow_step_progress(
                    step_key=step_id,
                    status="running",
                    title=task_text[:60],
                    detail=skill_name or "",
                )

                step_event_bus = StepEventBus(self._event_bus, step_id)

                try:
                    sub_report = self._run_specialist_task(
                        task_text=task_text or f"执行步骤: {step_id}",
                        skill_name=skill_name,
                        parent_context=context,
                        step_key=step_id,
                        step_event_bus=step_event_bus,
                        delegate_cwd=workdir or None,
                    )
                except Exception as e:
                    logger.error("[DAG并行] 步骤 %s 异常: %s", step_id, e)
                    self._emit_workflow_step_progress(
                        step_key=step_id,
                        status="error",
                        title=task_text[:60],
                        outcome=str(e)[:200],
                    )
                    plan_step = plan.find_step(step_id) if plan else None
                    if plan_step:
                        with self._plan_lock:
                            plan_step["status"] = "error"
                            plan_step["error_message"] = str(e)[:200]
                    return (step_id, {"status": "error", "summary": str(e)[:200], "artifacts": []})

                step_status = "completed" if sub_report.completed else "error"
                output = sub_report.summary
                artifacts = sub_report.artifacts

                if plan:
                    plan_step = plan.find_step(step_id)
                    if plan_step:
                        with self._plan_lock:
                            plan_step["status"] = step_status

                self._emit_workflow_step_progress(
                    step_key=step_id,
                    status=step_status,
                    title=task_text[:60],
                    outcome=output[:200] if output else "",
                )
                return (step_id, {
                    "status": step_status,
                    "summary": output[:500] if output else "",
                    "artifacts": artifacts,
                    "sub_session_id": sub_report.sub_session_id,
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
        """最终产物校验：检查工具返回的 artifacts 是否真实存在。

        仅记录日志并发送事件，不再把 warning 追加到 final_output，避免污染返回结果。
        """
        if not agent_result:
            return
        output_dir = self._get_output_dir()
        if not output_dir or not os.path.isdir(output_dir):
            return

        missing = self._find_missing_artifacts(agent_result.artifacts or [], output_dir)

        if missing:
            warning = f"⚠️ 以下产物未在输出目录中找到: {', '.join(missing[:5])}"
            logger.warning("[产物校验] %s", warning)
            self._event_bus.emit({"type": "artifact_warning", "content": warning})

    @staticmethod
    def _find_missing_artifacts(artifacts: List[str], output_dir: str) -> List[str]:
        """返回在 output_dir 中不存在的产物路径列表。

        - 绝对路径：直接判断是否存在。
        - 相对路径：按 output_dir 解析后判断。
        """
        missing: List[str] = []
        out_path = Path(output_dir)
        for path_str in artifacts:
            if not path_str:
                continue
            p = Path(path_str)
            if p.is_absolute():
                exists = p.is_file()
            else:
                exists = (out_path / p).is_file()
            if not exists:
                missing.append(str(p))
        return missing

    def _record_workflow_step_event(
        self,
        step_key: str,
        status: str,
        title: str = "",
        detail: str = "",
        outcome: str = "",
        subtasks: Optional[List[dict]] = None,
    ) -> None:
        """记录 workflow_step 到 trace（幂等，不依赖 EventBus）。"""
        if self._tracing_service is None:
            return
        session_id = self.session_id or (self._current_run_context.session_id if self._current_run_context else "")
        self._tracing_service.record_event(
            session_id,
            "workflow",
            "workflow_step",
            input={
                "step_key": step_key,
                "status": status,
                "title": title,
                "detail": detail,
                "outcome": outcome,
                "subtasks": subtasks or [],
            },
        )

    def _step_subtasks(self, step_key: str) -> List[dict]:
        """从当前计划中获取指定步骤的 subtasks，用于 workflow_step 事件。"""
        plan = self._last_loop_state.plan if self._last_loop_state else None
        if not plan:
            return []
        step = plan.find_step(step_key)
        return step.get("subtasks", []) if step else []

    def _emit_workflow_step_progress(
        self,
        step_key: str,
        status: str,
        title: str = "",
        detail: str = "",
        outcome: str = "",
        subtasks: Optional[List[dict]] = None,
    ) -> None:
        """同时向 EventBus 和 trace 发送 workflow_step 事件，并自动附带 subtasks。"""
        subtasks = subtasks if subtasks is not None else self._step_subtasks(step_key)
        self._event_bus.emit_workflow_step(
            step_key=step_key,
            status=status,
            title=title,
            detail=detail,
            outcome=outcome,
            subtasks=subtasks,
        )
        self._record_workflow_step_event(
            step_key=step_key,
            status=status,
            title=title,
            detail=detail,
            outcome=outcome,
            subtasks=subtasks,
        )

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
    def _set_session_context(session_id: str, output_dir: str, delegate_cwd: Optional[str] = None) -> None:
        try:
            from floodmind.tools.base_tools import set_session_context
            set_session_context(session_id, output_dir, delegate_cwd=delegate_cwd)
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

    def _get_output_dir(self, session_id: Optional[str] = None) -> str:
        """主代理产物目录：优先 workspace.user_dir；回退到 session_manager 旧路径。"""
        ws = get_workspace()
        if ws is not None:
            d = str(ws.user_dir)
            os.makedirs(d, exist_ok=True)
            return d
        sid = session_id or self.session_id
        if sid:
            data_dir = os.environ.get('DATA_DIR', str(Path.cwd() / "data"))
            output_dir = os.path.join(data_dir, "sessions", sid, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            return output_dir
        d = get_current_session_output_dir()
        if d:
            return d
        return os.path.abspath(os.path.join("data", "agent_state"))

    def _get_upload_dir(self, session_id: Optional[str] = None) -> str:
        """uploads 属 session 管理横切：优先 workspace.session_root，回退旧路径。"""
        sid = session_id or self.session_id
        ws = get_workspace()
        if ws is not None and sid:
            upload_dir = ws.session_root / sid / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            return str(upload_dir)
        if sid:
            data_dir = os.environ.get('DATA_DIR', str(Path.cwd() / "data"))
            upload_dir = os.path.join(data_dir, "sessions", sid, "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            return upload_dir
        return ""

    def stream(
        self,
        user_input: str,
        enable_reasoning: bool = False,
        user_message: str = "",
        attachments: Optional[List[Attachment]] = None,
        abort_check: Optional[Any] = None,
        resume_session_id: Optional[str] = None,
        resume_checkpoint_id: Optional[str] = None,
    ):
        """
        流式运行智能体，通过 Queue + threading 实现真实流式。
        与 FloodAgent.stream() 输出格式兼容。

        Args:
            resume_session_id: 如果提供，从该 session 的最新 checkpoint 恢复执行。
            resume_checkpoint_id: 如果提供，从指定 checkpoint 恢复（需配合 resume_session_id）。
        """
        try:
            logger.info("NativeFloodAgent 收到用户输入(流式): %s...", user_input[:50])
            _active_input_var.set(user_message or user_input)
            self._active_user_message = user_message or user_input

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

                    # 是否从 checkpoint 恢复
                    effective_session_id = resume_session_id or self.session_id

                    output_dir = self._get_output_dir(effective_session_id)
                    upload_dir = self._get_upload_dir(effective_session_id)
                    self._set_session_context(effective_session_id, output_dir)

                    self._artifact_watcher = ArtifactWatcher(output_dir=output_dir, upload_dir=upload_dir)
                    self._artifact_watcher.take_snapshot()

                    context = RunContext(
                        session_id=effective_session_id,
                        user_text=user_input,
                        attachments=attachments or [],
                        output_dir=output_dir,
                        upload_dir=upload_dir,
                        enable_reasoning=enable_reasoning,
                        abort_check=None,
                    )
                    self._current_run_context = context

                    # 包装 abort_check：同时检查用户中断
                    def _effective_abort_check():
                        if abort_check and abort_check():
                            return True
                        return False

                    context.abort_check = _effective_abort_check

                    from floodmind.agent.runtime.services.ask_service import get_ask_service
                    ask_service = get_ask_service()
                    ask_service.set_emit_fn(lambda event: self._event_bus.emit(event), session_id=effective_session_id)

                    # 新会话开始时重置重试保护状态，避免跨任务累积误拦合法调用
                    try:
                        from floodmind.tools.base_tools import reset_retry_guard
                        reset_retry_guard()
                    except Exception as e:
                        logger.warning("reset_retry_guard 失败（非致命）: %s", e)

                    # 初始化 trace 上下文并挂载到 EventBus
                    self._tracing_service.set_trace_context(effective_session_id, trace_id=effective_session_id)
                    self._tracing_service.register_event_bus(self._event_bus, effective_session_id)

                    # memory 是唯一历史源：每次 stream 都从 memory 起步构建上下文。
                    # 不再从 checkpoint 恢复——暂停 = abort 丢弃当前未完成轮，
                    # 已完成的轮已原子落入 memory，下一次 stream 天然从 memory 续上。
                    self._last_loop_state = AgentLoopState(
                        session_id=effective_session_id,
                        run_id=f"run-{int(time.time())}",
                        user_message=user_input,
                        original_input=user_input,
                    )
                    state = self._last_loop_state

                    # 构建 memory messages：经验上下文 + 精简对话历史（不含全量，控 token / 防长文本幻觉）
                    experience_context = self._build_experience_context()
                    history_text = ""
                    if hasattr(self.memory, "get_chat_history_for_system_prompt"):
                        _sp = getattr(self._orchestrator_executor, "system_prompts", None)
                        context_chars = len(_sp[0]) if _sp else 0
                        cw = settings.agent.context_window
                        history_text = self.memory.get_chat_history_for_system_prompt(
                            total_context_chars=context_chars,
                            context_window=cw,
                            event_bus=self._event_bus,
                        ) or ""

                    memory_messages = []
                    if experience_context:
                        memory_messages.append({"role": "system", "content": experience_context})
                    if history_text:
                        memory_messages.append({"role": "system", "content": history_text})

                    state.messages = self._orchestrator_executor._build_initial_messages(
                        context=context,
                        user_text=user_input,
                        attachments=attachments or [],
                        memory_messages=memory_messages,
                    )

                    saved_extra = getattr(self._orchestrator_executor, "extra_body", None)
                    if enable_reasoning:
                        self._orchestrator_executor.extra_body = {"enable_thinking": True}
                    else:
                        self._orchestrator_executor.extra_body = {}

                    self._event_bus.set_queue(q)

                    agent_result = self._orchestrator_executor.run_from_state(
                        context=context,
                        state=state,
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
                    try:
                        from floodmind.agent.runtime.services.ask_service import get_ask_service
                        get_ask_service().clear_emit_fn(session_id=effective_session_id)
                    except Exception:
                        pass
                    try:
                        self._tracing_service.flush(effective_session_id)
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

            # ──文件/image 生成事件──
            if agent_result and agent_result.artifacts:
                import os as _os
                for artifact_path in agent_result.artifacts:
                    try:
                        fname = _os.path.basename(artifact_path)
                        ext = _os.path.splitext(fname)[1].lower()
                        if ext in ('.png','.jpg','.jpeg','.gif','.webp','.bmp'):
                            self._event_bus.emit_image_generated(fname, artifact_path)
                        else:
                            self._event_bus.emit_file_generated(fname, artifact_path)
                    except Exception:
                        pass
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
                # DualMemory（支持 add_assistant_round）：每个完整 LLM 调用轮已由 executor
                # 原子写入 memory，这里不再重复写终态轮，避免重复。
                # memory 为 None（子代理）或异常未落轮时，由 save_chat_history 兜底。
                if not hasattr(self.memory, "add_assistant_round"):
                    if hasattr(self.memory, "add_ai_message_with_trace"):
                        self.memory.add_ai_message_with_trace(full_answer, full_reasoning, full_tool_calls)
                    elif hasattr(self.memory, "add_ai_message"):
                        self.memory.add_ai_message(full_answer)
                # 持久化对话历史（兜底：确保 user entry 等落盘）
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

            if agent_result and agent_result.is_timeout:
                logger.warning("NativeFloodAgent 流式执行超时")
            else:
                logger.info("NativeFloodAgent 流式执行成功")

            self._event_bus.clear_queue()

        except Exception as e:
            logger.error("NativeFloodAgent 流式执行失败: %s", e)
            # 错误必须以 error 类型上报：web_server 会把 reasoning/thought_delta 归一为
            # thought_delta，若这里用 reasoning，错误信息会被前端渲染成"思考"块而非错误块。
            yield {"type": "error", "content": f"抱歉，处理您的请求时出错了：{str(e)}"}
        finally:
            _active_input_var.set("")

    def run(self, user_input: str) -> str:
        """非流式运行（收集所有流式事件后返回最终回答）。"""
        return self.run_with_resume(user_input)

    def run_with_resume(
        self,
        user_input: str,
        resume_session_id: Optional[str] = None,
        resume_checkpoint_id: Optional[str] = None,
    ) -> str:
        """非流式运行，支持从 checkpoint 恢复。"""
        full_answer = ""
        for event in self.stream(
            user_input,
            resume_session_id=resume_session_id,
            resume_checkpoint_id=resume_checkpoint_id,
        ):
            if event.get("type") == "final_text":
                full_answer = event.get("content", "")
            elif event.get("type") == "token" and not full_answer:
                full_answer += event.get("content", "")
        return full_answer or "抱歉，处理您的请求时未能生成回答。"

    def pause(self, session_id: Optional[str] = None) -> bool:
        """暂停：统一为“中止当前流 + 丢弃未完成轮”。

        实际中断由调用方（web_server）通过传入 stream() 的 abort_check 触发——
        executor 在 LLM 流/工具边界检测到 abort 后，丢弃当前未完成轮（不落 history），
        终态 failed。已完成轮已在 memory，下一次发送天然续上。
        本方法保留接口兼容，不再操作 checkpoint（checkpoint 暂停路径已废弃）。
        """
        target_session_id = session_id or self.session_id
        return bool(target_session_id)

    def chat(self, message: str) -> str:
        return self.run(message)

    def get_memory_summary(self) -> Dict[str, Any]:
        if hasattr(self.memory, "to_dict"):
            return self.memory.to_dict()
        return {}

    def clear_memory(self):
        if hasattr(self.memory, "clear"):
            self.memory.clear()
        logger.info("NativeFloodAgent 记忆已清空")
