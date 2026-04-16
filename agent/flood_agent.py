"""
洪水预报智能体模块

基于LangChain框架实现的洪水预报智能体，集成工具调用、记忆系统和大模型。
使用 OpenAI Functions Agent，利用模型的 Function Calling 能力，避免文本解析错误。

Prompt 结构：
1. 系统提示（System Prompt）：始终完整保留，包含角色定义、工具说明、工作流程、工作原则
2. 长期记忆（Long-term Memory）：作为独立上下文注入，存储重要信息
3. 会话历史（Chat History）：短期对话记忆，会被压缩
"""

import logging
import json
import os
import queue
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from langchain_classic.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from pydantic import BaseModel, Field

from models.qwen_llm_service import QwenLLMService
from memory import SimpleMemory, DualMemory
from skills import SKILL_REGISTRY
from skills.base import Skill
from tools import get_skill, run_script, exec_bash, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, check_artifact_exists, read_artifact, knowledge_search, add_knowledge, web_search, add_memory, search_memory, set_rag_config, set_memory_instance, reset_retry_guard
from config.settings import settings

logger = logging.getLogger(__name__)


class SpecialistTaskInput(BaseModel):
    task: str = Field(default="", description="交给执行单元的明确任务说明，应尽量具体、短小、可执行")
    skill_name: str = Field(default="", description="若当前任务明确要求复用某个 skill，则传入对应的 skill 名称")


class _FunctionsStreamCallback(BaseCallbackHandler):
    """OpenAI Functions Agent 的流式输出回调"""

    def __init__(self, q: queue.Queue, enable_reasoning: bool = False):
        super().__init__()
        self._q = q
        self._current_tool_name = None
        self._current_tool_input = ""
        self._enable_reasoning = enable_reasoning
        self._reasoning_buffer = ""
        self._full_reasoning_content = ""
        self._is_in_thinking_phase = False
        self._thinking_content = ""
        self._pending_tokens = []
        self._has_tool_call = False
        self._first_llm_call = True
        self._initial_enable_reasoning = enable_reasoning
        self._reasoning_emit_buffer = ""
        logger.info(f"[Reasoning Callback] 初始化, enable_reasoning={enable_reasoning}")

    def _flush_reasoning_buffer(self) -> None:
        if not self._reasoning_emit_buffer:
            return
        self._q.put(("reasoning", self._reasoning_emit_buffer))
        self._reasoning_emit_buffer = ""

    def _append_reasoning_chunk(self, text: str) -> None:
        chunk = str(text or "")
        if not chunk:
            return
        self._reasoning_emit_buffer += chunk
        normalized = self._reasoning_emit_buffer.strip()
        if not normalized:
            return

        if len(normalized) >= 48 or normalized.endswith(("。", "！", "？", "\n", ".", "!", "?", ":", "：", "；", ";")):
            self._flush_reasoning_buffer()

    def on_llm_start(self, serialized, prompts, **kwargs):
        """LLM 开始生成"""
        if self._enable_reasoning != self._initial_enable_reasoning:
            logger.warning(f"[LLM Start] _enable_reasoning 被意外修改: {self._enable_reasoning} -> {self._initial_enable_reasoning}")
            self._enable_reasoning = self._initial_enable_reasoning
        logger.info(f"[LLM Start] _has_tool_call={self._has_tool_call}, _enable_reasoning={self._enable_reasoning}")
        self._is_in_thinking_phase = self._enable_reasoning
        self._thinking_content = ""
        self._pending_tokens = []
        self._has_tool_call = False
        logger.info(f"[LLM Start] _is_in_thinking_phase={self._is_in_thinking_phase}")

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        """LLM 生成新 token"""
        if self._enable_reasoning:
            chunk = kwargs.get('chunk')
            if chunk and hasattr(chunk, 'message'):
                msg = chunk.message

                reasoning = None
                if hasattr(msg, 'reasoning_content'):
                    reasoning = msg.reasoning_content
                elif hasattr(msg, 'additional_kwargs'):
                    additional_kwargs = msg.additional_kwargs
                    if additional_kwargs:
                        reasoning = additional_kwargs.get('reasoning_content')

                if reasoning:
                    reasoning_text = str(reasoning)
                    # DashScope OpenAI 兼容接口返回的是增量 delta.reasoning_content；
                    # 但为了兼容少数可能返回累计文本的提供方，这里同时支持两种模式。
                    if self._reasoning_buffer and reasoning_text.startswith(self._reasoning_buffer):
                        new_reasoning = reasoning_text[len(self._reasoning_buffer):]
                        self._reasoning_buffer = reasoning_text
                    else:
                        new_reasoning = reasoning_text
                        self._reasoning_buffer += reasoning_text

                    if new_reasoning:
                        self._append_reasoning_chunk(new_reasoning)
                        return

            if not self._is_in_thinking_phase and token:
                self._q.put(("token", token))
        else:
            # 无 reasoning 模式下也先缓冲 token；
            # 如果随后发生工具调用，说明这些内容只是内部规划，不应透传给前端。
            if token:
                self._pending_tokens.append(token)

    def on_llm_end(self, response, **kwargs):
        """LLM 生成结束"""
        logger.info(f"[LLM End] _is_in_thinking_phase={self._is_in_thinking_phase}, _has_tool_call={self._has_tool_call}, pending_tokens={len(self._pending_tokens)}")
        # 不在这里发送 pending_tokens，因为此时还不知道是否有工具调用
        # pending_tokens 会在 on_tool_start 或 _run finally 中发送
        self._flush_reasoning_buffer()
        self._is_in_thinking_phase = False

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs) -> None:
        """工具开始执行"""
        tool_name = serialized.get("name", "unknown")
        logger.info(f"[Tool Start] 工具: {tool_name}, pending_tokens={len(self._pending_tokens)}, thinking_content_len={len(self._thinking_content)}")

        self._flush_reasoning_buffer()
        self._pending_tokens = []
        self._thinking_content = ""

        # 标记有工具调用开始
        self._has_tool_call = True
        self._current_tool_name = tool_name
        self._current_tool_input = input_str or ""
        self._q.put(("tool_status", {"tool_name": tool_name, "status": "running"}))

    def on_tool_end(self, output: str, **kwargs) -> None:
        """工具执行结束"""
        tool_name = self._current_tool_name
        output_str = str(output)

        if tool_name:
            self._q.put(("tool_result", {
                "tool_name": tool_name,
                "tool_input": self._current_tool_input,
                "content": output_str,
            }))

        # 如果是 web_search 工具，单独标记搜索结果
        if tool_name == "web_search":
            try:
                import json
                search_data = json.loads(output_str) if output_str.strip().startswith("[") else None
                if search_data and isinstance(search_data, list):
                    self._q.put(("search_result", output_str))
                else:
                    self._q.put(("search_result", output_str))
            except:
                self._q.put(("search_result", output_str))

        self._current_tool_name = None
        self._current_tool_input = ""
        # 重置 _has_tool_call，这样下一次 LLM 调用时可以启用思考阶段
        self._has_tool_call = False

    def on_tool_error(self, error: BaseException, **kwargs) -> None:
        """工具执行错误"""
        self._q.put(("tool_status", {
            "tool_name": self._current_tool_name or "unknown",
            "status": "error",
            "content": str(error),
        }))
        self._current_tool_name = None
        self._current_tool_input = ""

    def on_chain_start(self, serialized, inputs, **kwargs):
        """链开始"""
        pass

    def on_agent_action(self, action, **kwargs) -> None:
        """忽略 verbose agent action 文本，避免把调试轨迹当作思考过程。"""
        return

    def on_text(self, text: str, **kwargs) -> None:
        """忽略 verbose 中间文本，思考卡片只展示原生 reasoning 与摘要事件。"""
        return

    def on_chain_end(self, outputs, **kwargs):
        """链结束"""
        pass

    def on_chain_error(self, error: BaseException, **kwargs) -> None:
        """链错误"""
        self._flush_reasoning_buffer()
        self._q.put(("reasoning", str(error)))

    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        """LLM 错误"""
        self._flush_reasoning_buffer()
        self._q.put(("reasoning", str(error)))


@dataclass
class PlanStep:
    step_id: str
    title: str
    executor: str
    status: str = "pending"
    purpose: str = ""
    input_text: str = ""
    output_text: str = ""
    verification: Dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 0


@dataclass
class AgentLoopState:
    original_input: str
    plan_steps: List[PlanStep] = field(default_factory=list)
    previous_outputs: List[Tuple[str, str]] = field(default_factory=list)
    tool_calls: List[Dict[str, str]] = field(default_factory=list)
    final_output: str = "抱歉，我无法回答这个问题。"
    latest_payload: Optional[Dict[str, Any]] = None
    artifacts: List[str] = field(default_factory=list)
    final_check: Dict[str, Any] = field(default_factory=dict)
    round_count: int = 0
    replan_count: int = 0
    terminal_status: str = "running"
    execution_journal: List[Dict[str, Any]] = field(default_factory=list)
    artifact_registry: List[Dict[str, Any]] = field(default_factory=list)


class FloodAgent:
    """洪水预报智能体类 - 使用 OpenAI Functions Agent"""
    SYSTEM_PROMPT = """你是大水云开发的调度 agent。

## 当前系统时间
{current_time_context}

## 角色职责
你只负责四类事情：
1. 分析用户意图和最终目标
2. 规划任务步骤
3. 把步骤分发给执行单元
4. 汇总执行结果并回答用户

## 角色边界
- 不负责亲自编写脚本、执行脚本、生成 Excel、构造 input.json、运行模型等具体任务
- 不负责在没有校验的情况下直接宣布任务完成

## 可用工具
- `get_skill`：查看 skill 的详细说明、脚本、参数和规则
- `search_artifacts`：搜索当前会话或历史可复用产物
- `read_artifact`：读取文本类产物
- `knowledge_search`：检索知识库（知识查询时，优先使用）
- `web_search`：检索网络资料（knowledge_search搜索结果不够支撑回答时，搜索网络资料补充）
- `search_memory`：检索历史对话和技能文档

## 可用执行单元
- `delegate_execution_specialist`：执行单步落地任务，包括数据提取、转换、结构化文件生成、Excel 导出、模型相关脚本执行；如已明确 skill，委派时一并传 `skill_name`

## 敖江流域子任务编码
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~水动力模型区间断面预报`、`水动力模型区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`
- 详细信息查看 aojiang-hydro SKILL.md文档

## 可用 skills
{skill_catalog}

## 调度工作流
### 1. 分析目标
先明确：
1. 用户最终要什么交付物
2. 当前输入属于原始数据、中间结果还是最终结果
3. 当前缺的是哪一个阶段

### 2. 优先确认 skill
如果任务明显对应某个业务 skill 或导出能力，必须先调用 `get_skill` 查看详细说明，再决定下一步。

### 3. 规划并分发
每次分发给 `delegate_execution_specialist` 的任务必须满足：
1. 只有一个核心动作
2. 明确输入文件或输入产物
3. 明确预期输出
4. 明确必要约束或应参考的 skill
5. 不要把用户原始长文本整包塞给执行单元
6. 如果你已经确定要复用某个 skill，必须显式传入 `skill_name`

合格示例：
- task: 把 `./input.xlsx` 转为 `./input.json`，输入表包含 `time` 和 `rainfall` 两列；skill_name: `hydro-input-prep`
- task: 使用 `normalize_hydro_input_file_to_excel.py` 将上传的 `2.xlsx` 转为标准中间 Excel；skill_name: `hydro-input-prep`
- task: 基于 `./result.json` 生成最终 `./result.xlsx`，优先复用对应业务 skill 已声明的导出脚本；skill_name: `<对应业务 skill>`
- 读取 `./result.xlsx` 中的 `rainfall` 列并绘制时序图，输出 `./rainfall.png`

### 4. 结合校验继续推进
只有当本轮任务明确承诺了文件产物时，才在流程结束后执行代码级最终文件存在性检查。
如果最终文件检查明确指出缺失文件，优先按缺失文件结果继续分发，不要自己重新写成模糊任务。

### 5. 整理最终回答
最终只向用户总结：
1. 已完成什么
2. 生成了哪些最终文件
3. 如果未完成，还缺什么

## 调度原则
1. 只做规划、分发、汇总
2. 如果有相关 skill，先查 skill，再决定是否委派
3. 对执行单元只传当前这一步的执行指令，不传用户原始长输入和多余会话背景
4. 严禁把超长 JSON 直接塞进任务描述
5. 必须严格遵循 SKILL.md 及相关文档

## 输出规范
- 最终输出不要包含系统完整路径
- 最终输出不要包含会话环境内部信息
- 最终只返回用户需要知道的文件名和结果
- 最终输出为标准 Markdown
"""

    EXECUTION_SPECIALIST_PROMPT = """你是 Execution Specialist 执行单元。

## 你的职责
你只负责：
1. 执行调度 agent 已明确分配的单步任务
2. 数据提取、清洗、转换
3. 构造中间结构化文件，如 `input.json`、`.json`、`.csv`
4. 生成 Excel/CSV/TSV 等表格结果
5. 根据调度任务运行已有 skill 脚本
6. 在没有可复用 skill/脚本时，编写并执行临时 Python 脚本完成任务

## 执行原则
- 把输入任务视为已定稿的执行指令，不要重写目标，不要重新拆解流程，不要补充下游计划
- 优先复用已有 skill、已有脚本、已有中间结果；只有缺少直接能力时才写临时脚本
- 只围绕当前这一步行动；做完立即返回，不扩展上下游
- 如果指令缺文件、缺参数、缺前置产物，就明确指出缺什么，不要自己猜业务意图

## 强约束
- 不要重新理解用户需求；按当前任务执行
- 不要猜测 skill 未声明的脚本、参数或字段
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

## 可使用skills
{skill_catalog}

## 输出要求
- 简洁说明本次任务是否完成
- 明确返回直接结果，如生成文件路径、读取/搜索结果、关键输出摘要
- 不要给出下一步建议，不要说明后续如何使用，由调度 agent 决定后续动作
"""

    def __init__(
        self,
        llm_service: QwenLLMService,
        memory: Optional[Any] = None,
        skills: Optional[List[Skill]] = None,
        enable_chronos_warmup: Optional[bool] = None,
        **kwargs,
    ):
        """
        初始化洪水预报智能体

        Args:
            llm_service: Qwen大模型服务实例
            memory:      记忆系统实例（可选，默认使用 DualMemory）
            skills:      技能列表（可选，默认使用 SKILL_REGISTRY）
        """
        self.llm_service = llm_service

        if memory is not None:
            self.memory = memory
        else:
            self.memory = DualMemory(
                max_history=kwargs.get("max_history", 20),
                context_window=kwargs.get("context_window", 32768),
            )
            logger.info("使用双层记忆系统（DualMemory）")

        if hasattr(self.memory, 'set_llm') and self.memory.llm is None:
            self.memory.set_llm(llm_service.get_llm())

        if hasattr(self.memory, 'load_chat_history'):
            try:
                self.memory.load_chat_history()
                logger.info("对话历史已从磁盘加载")
            except Exception as e:
                logger.error(f"加载对话历史失败: {e}")

        set_memory_instance(self.memory)

        set_rag_config(
            enabled=settings.rag.enabled,
            persist_dir=settings.rag.persist_dir,
            embedding_model=settings.rag.embedding_model,
            top_k=settings.rag.top_k,
        )

        self.skills: List[Skill] = skills if skills is not None else SKILL_REGISTRY
        self.base_tools: List[BaseTool] = [get_skill, search_artifacts, read_artifact, knowledge_search, web_search, search_memory]

        skill_catalog = "\n".join(
            f"- {s.name}: {s.description}" for s in self.skills
        ) + "\n- get_skill: 按需获取任意技能的完整参数说明"

        self._skill_catalog = skill_catalog
        self._active_user_input = ""
        self._last_loop_state: Optional[AgentLoopState] = None

        self.execution_tools: List[BaseTool] = [get_skill, run_script, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, read_artifact]

        self.execution_specialist_executor = self._build_specialized_executor(self.EXECUTION_SPECIALIST_PROMPT, self.execution_tools, max_iterations=50)

        self._executors: Dict[str, AgentExecutor] = {
            "execution_specialist": self.execution_specialist_executor,
        }

        self.delegation_tools: List[BaseTool] = self._build_delegation_tools()
        self.tools: List[BaseTool] = self.base_tools + self.delegation_tools
        logger.info(f"加载技能: {[s.name for s in self.skills]}，工具数: {len(self.tools)}")

        self.prompt = self._build_prompt(self.SYSTEM_PROMPT)
        self.agent = create_openai_functions_agent(
            llm=llm_service.get_llm(),
            tools=self.tools,
            prompt=self.prompt,
        )
        self.agent_executor = self._create_executor(self.agent, self.tools, max_iterations=50, max_execution_time=600)
        self._executors["orchestrator"] = self.agent_executor

        logger.info("洪水预报智能体初始化成功（OpenAI Functions Agent）")
        self._warmup_chronos()

    def _build_prompt(self, system_prompt: str, *, include_memory: bool = True, include_chat_history: bool = True) -> ChatPromptTemplate:
        messages: List[Any] = [
            ("system", system_prompt.format(skill_catalog=self._skill_catalog, current_time_context="{current_time_context}")),
        ]
        if include_memory:
            messages.append(MessagesPlaceholder(variable_name="long_term_memory", optional=True))
        if include_chat_history:
            messages.append(MessagesPlaceholder(variable_name="chat_history", optional=True))
        messages.extend([
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        return ChatPromptTemplate.from_messages(messages)

    def _create_executor(self, agent, tools: List[BaseTool], max_iterations: int = 30, max_execution_time: int = 600) -> AgentExecutor:
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            max_iterations=max_iterations,
            return_intermediate_steps=True,
            max_execution_time=max_execution_time,
            handle_parsing_errors=True,
            early_stopping_method="force",
        )

    def _build_specialized_executor(self, system_prompt: str, tools: List[BaseTool], max_iterations: int = 30) -> AgentExecutor:
        prompt = self._build_prompt(system_prompt, include_memory=False, include_chat_history=False)
        agent = create_openai_functions_agent(
            llm=self.llm_service.get_llm(),
            tools=tools,
            prompt=prompt,
        )
        return self._create_executor(agent, tools, max_iterations=max_iterations, max_execution_time=600)

    @staticmethod
    def _parse_task_sections(task: str) -> Tuple[str, List[Tuple[str, str]]]:
        text = (task or "").strip()
        if not text:
            return "", []

        first_section_match = re.search(r"\n\s*\[[^\n\]]+\]\s*\n", text)
        if not first_section_match:
            return text, []

        core_task = text[:first_section_match.start()].strip()
        remainder = text[first_section_match.start():].strip()
        section_pattern = re.compile(
            r"\[(?P<header>[^\n\]]+)\]\s*\n(?P<content>.*?)(?=\n\s*\[[^\n\]]+\]\s*\n|\Z)",
            flags=re.DOTALL,
        )
        sections = [
            (match.group("header").strip(), match.group("content").strip())
            for match in section_pattern.finditer(remainder)
        ]
        if core_task:
            return core_task, sections

        prioritized_headers = ("核心任务", "当前待完成目标", "当前子任务", "下一步动作")
        for header, content in sections:
            if header in prioritized_headers and content:
                first_line = content.splitlines()[0].strip()
                if first_line:
                    return first_line, sections

        return text, sections

    @staticmethod
    def _truncate_block(text: str, limit: int) -> str:
        normalized = (text or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "\n...(已截断，保留前部关键信息)"

    @staticmethod
    def _build_goal_brief(user_input: str) -> str:
        text = re.sub(r"\s+", " ", str(user_input or "").strip())
        if not text:
            return "用户最终目标尚未明确"

        markers = ["请", "帮我", "需要", "想要", "目标", "最终"]
        for marker in markers:
            index = text.find(marker)
            if index >= 0:
                candidate = text[index:].strip(" ：:，,。；;")
                if candidate:
                    return candidate[:120]
        return text[:120]

    def _build_specialist_user_input(self, stage_name: str, task: str, skill_name: str = "") -> str:
        normalized_task = (task or "").strip()
        normalized_skill_name = (skill_name or "").strip()
        if not normalized_task:
            return ""

        core_task, sections = self._parse_task_sections(normalized_task)
        lines: List[str] = []

        lines.extend([
            "你现在只执行一个已经明确分配的子任务。",
            "把它当作执行指令，不要重写目标，不要补充计划，不要重新理解用户需求。",
            "如果存在[核心任务]，只围绕[核心任务]执行；其他内容仅作最小必要参考。",
            "",
            "[核心任务]",
            core_task or normalized_task,
        ])

        if normalized_skill_name:
            lines.extend([
                "",
                "[指定skill]",
                normalized_skill_name,
                "",
                "如果当前任务明确要求复用该 skill，优先先调用 `get_skill` 查看其脚本与参数，再执行。",
            ])

        for header, content in sections:
            if not content:
                continue
            if header in {"当前待完成目标", "输入文件", "可用中间文件", "预期输出", "执行约束", "可复用产物"}:
                lines.extend([
                    "",
                    f"[{header}]",
                    self._truncate_block(content, 500),
                ])

        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_executor_name(stage_name: str) -> str:
        return str(stage_name or "").strip()

    def _run_specialist_task(self, stage_name: str, task: str, skill_name: str = "") -> str:
        stage_name = self._normalize_executor_name(stage_name)
        task = (task or "").strip()
        skill_name = (skill_name or "").strip()
        if not task:
            return f"错误：委派给 {stage_name} 的 task 不能为空"

        executor = self._executors.get(stage_name)
        if executor is None:
            return f"错误：未找到执行单元 `{stage_name}`"

        specialist_input = self._build_specialist_user_input(stage_name, task, skill_name)
        result = self._invoke_executor(executor, specialist_input, include_session_context=False)
        self._record_intermediate_steps(result)
        output = (result.get("output", "") or "").strip()
        output = self._normalize_specialist_output(result, output)
        if not output:
            return f"{stage_name} 未返回有效结果"

        stage_label = {
            "execution_specialist": "Execution Specialist",
        }.get(stage_name, stage_name)

        payload = {
            "stage": stage_name,
            "stage_label": stage_label,
            "result_type": "intermediate",
            "status": "completed",
            "user_goal": self._active_user_input,
            "task": task,
            "skill_name": skill_name,
            "summary": output,
            "artifacts": self._extract_artifact_paths(output),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_delegation_tools(self) -> List[BaseTool]:
        def delegate_execution_specialist(task: str = "", skill_name: str = "") -> str:
            return self._run_specialist_task("execution_specialist", task, skill_name)

        return [
            StructuredTool.from_function(
                func=delegate_execution_specialist,
                name="delegate_execution_specialist",
                description="当你已经完成任务拆分，且需要执行单元无脑执行某一个明确步骤时调用。适用于数据提取、文件转换、中间 JSON/CSV、Excel 导出、运行 skill 脚本、编写最小临时 Python 脚本等单步落地任务。若已明确要复用某个 skill，必须同时传入 skill_name。",
                args_schema=SpecialistTaskInput,
            ),
        ]

    @staticmethod
    def _warmup_chronos():
        """后台预热 Chronos-2 模型，避免首次预测冷启动延迟"""
        def _warmup():
            try:
                from skills.chronos_pipeline import get_pipeline
                get_pipeline()
            except Exception as e:
                logger.warning(f"Chronos-2 预热失败（不影响功能）: {e}")
        t = threading.Thread(target=_warmup, daemon=True, name="chronos-warmup")
        t.start()
        logger.info("Chronos-2 后台预热已启动")

    def _get_chat_history_messages(self) -> List[BaseMessage]:
        """获取对话历史消息列表"""
        if hasattr(self.memory, 'get_messages'):
            return self.memory.get_messages()
        return []

    def _get_long_term_memory_message(self) -> Optional[SystemMessage]:
        """获取长期记忆作为系统消息"""
        context = self.memory.get_long_term_context() if hasattr(self.memory, 'get_long_term_context') else ""
        if context:
            return SystemMessage(content=context)
        return None

    @staticmethod
    def _get_current_time_context() -> str:
        now = datetime.now().astimezone()
        timezone_name = now.tzname() or "本地时区"
        return (
            f"当前系统时间: {now.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
            f"ISO时间: {now.isoformat()}\n"
            f"当前时区: {timezone_name}\n"
            f"今天是: {now.strftime('%Y-%m-%d')}\n"
            f"当前星期: 星期{'一二三四五六日'[now.weekday()]}"
        )

    @staticmethod
    def _get_current_system_context() -> str:
        import platform
        shell_name = "powershell.exe / pwsh" if os.name == "nt" else "bash / sh"
        path_style = "Windows" if os.name == "nt" else "POSIX"
        return (
            f"操作系统: {platform.system()} {platform.release()}\n"
            f"Python 版本: {platform.python_version()}\n"
            f"exec_bash shell 策略: 自动选择当前可用 shell\n"
            f"当前环境优先 shell: {shell_name}\n"
            f"路径风格: {path_style}"
        )

    def _build_context_messages(self) -> List[SystemMessage]:
        """构造每轮都要注入的系统上下文消息。"""
        context_messages: List[SystemMessage] = []

        long_term_memory = self._get_long_term_memory_message()
        if long_term_memory:
            context_messages.append(long_term_memory)

        recent_result_message = self._get_recent_result_message()
        if recent_result_message:
            context_messages.append(recent_result_message)

        last_tool_use_message = self._get_last_tool_use_message()
        if last_tool_use_message:
            context_messages.append(last_tool_use_message)

        return context_messages

    @staticmethod
    def _looks_like_file_heavy_task(user_input: str) -> bool:
        text = (user_input or "").lower()
        markers = (
            ".xlsx", ".xls", ".csv", ".tsv", ".log", ".txt", ".json",
            "excel", "xlsx", "csv", "sheet", "工作表", "表格", "导出", "日志", "log", "断面",
            "input.json", "请求体", "解析", "提取", "转换", "脚本",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _select_execution_plan(user_input: str) -> List[str]:
        # 当前默认始终先由主 agent 统一理解和规划，避免在任务尚未澄清前
        # 就因为规则命中而提前切到执行单元，导致执行顺序和展示逻辑混乱。
        return ["orchestrator"]

    def _build_initial_loop_state(self, user_input: str) -> AgentLoopState:
        initial_step = PlanStep(
            step_id="step-1",
            title="理解目标并开始执行",
            executor="orchestrator",
            purpose="先由主 agent 理解最终目标、选择技能与执行单元，并产出第一轮结果。",
            input_text=user_input,
        )
        return AgentLoopState(original_input=user_input, plan_steps=[initial_step])

    @staticmethod
    def _get_next_pending_step(state: AgentLoopState) -> Optional[PlanStep]:
        for step in state.plan_steps:
            if step.status == "pending":
                return step
        return None

    @staticmethod
    def _collect_artifacts_from_payload(payload: Optional[Dict[str, Any]]) -> List[str]:
        if not payload:
            return []
        artifacts = payload.get("artifacts") or []
        result: List[str] = []
        for item in artifacts:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    def _extract_result_payload(self, stage_name: str, result: Dict[str, Any], stage_output: str) -> Optional[Dict[str, Any]]:
        text = (stage_output or "").strip()
        if not text:
            return self._extract_latest_delegation_payload(result)

        if self._normalize_executor_name(stage_name) == "execution_specialist":
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None

        delegated_payload = self._extract_latest_delegation_payload(result)
        output_artifacts = self._extract_artifact_paths(text)
        if output_artifacts:
            payload = dict(delegated_payload or {})
            payload.setdefault("stage", stage_name)
            payload.setdefault("result_type", "intermediate")
            payload.setdefault("user_goal", self._active_user_input)
            payload["summary"] = text
            payload["artifacts"] = output_artifacts
            if self._artifacts_satisfy_user_goal(payload):
                payload["result_type"] = "final"
            return payload

        return delegated_payload

    def _build_step_input(self, state: AgentLoopState, step: PlanStep) -> str:
        if step.input_text:
            return step.input_text

        latest_payload = state.latest_payload or {}

        if latest_payload:
            return self._build_forced_continuation_input(state.original_input, latest_payload)

        return state.original_input

    def _verify_step_result(self, step: PlanStep, payload: Optional[Dict[str, Any]], stage_output: str) -> Dict[str, Any]:
        base_progress = {
            "completed_steps": [],
            "pending_steps": [],
            "failed_step": None,
            "reusable_artifacts": self._collect_artifacts_from_payload(payload),
        }
        if payload:
            return {
                "scope": "step",
                "status": "pass",
                "step_completed": True,
                "reason": "",
                "goal_satisfied": False,
                "requires_replan": False,
                "final_check": {},
                **base_progress,
            }

        text = (stage_output or "").strip()
        return {
            "scope": "step",
            "status": "pass" if text else "fail",
            "step_completed": bool(text),
            "reason": "" if text else "当前步骤未产出有效结果",
            "goal_satisfied": False,
            "requires_replan": not bool(text),
            "final_check": {},
            **({
                **base_progress,
                "failed_step": {
                    "step_id": step.step_id,
                    "title": step.title,
                    "executor": step.executor,
                    "purpose": step.purpose,
                    "status": step.status,
                },
            } if not text else base_progress),
        }

    @staticmethod
    def _needs_final_deliverable(user_input: str) -> Dict[str, bool]:
        lower_text = (user_input or "").lower()
        return {
            "excel": any(marker in lower_text for marker in ("excel", ".xlsx", "工作表", "结果表", "导出表")),
            "image": any(marker in lower_text for marker in ("图片", "图", "过程线", "plot", ".png", ".jpg", ".jpeg")),
            "report": any(marker in lower_text for marker in ("报告", "docx", "word", ".docx", ".pdf", "pdf")),
        }

    @staticmethod
    def _artifacts_cover_needs(artifacts: List[str], needs: Dict[str, bool]) -> bool:
        lower_artifacts = [str(path or "").lower() for path in artifacts]
        has_excel = any(path.endswith(".xlsx") or path.endswith(".xls") for path in lower_artifacts)
        has_image = any(path.endswith(".png") or path.endswith(".jpg") or path.endswith(".jpeg") for path in lower_artifacts)
        has_report = any(path.endswith(".docx") or path.endswith(".pdf") for path in lower_artifacts)

        if needs["excel"] and not has_excel:
            return False
        if needs["image"] and not has_image:
            return False
        if needs["report"] and not has_report:
            return False
        return any([has_excel, has_image, has_report]) if any(needs.values()) else bool(lower_artifacts)

    @staticmethod
    def _classify_step_completion(state: AgentLoopState) -> Dict[str, Any]:
        completed_steps: List[Dict[str, str]] = []
        pending_steps: List[Dict[str, str]] = []
        failed_step: Optional[Dict[str, str]] = None

        for step in state.plan_steps:
            item = FloodAgent._build_step_progress_item(state, step)
            if step.status == "completed":
                completed_steps.append(item)
            elif step.status == "failed":
                failed_step = item
            else:
                pending_steps.append(item)

        return {
            "completed_steps": completed_steps,
            "pending_steps": pending_steps,
            "failed_step": failed_step,
        }

    @staticmethod
    def _summarize_text_block(text: str, limit: int = 160) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "").strip())
        if not normalized:
            return ""
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "..."

    @staticmethod
    def _get_step_artifacts(state: AgentLoopState, step_id: str) -> List[str]:
        artifacts: List[str] = []
        for record in state.artifact_registry:
            if str(record.get("producer_step_id", "") or "").strip() != step_id:
                continue
            path = str(record.get("path", "") or "").strip()
            if path and path not in artifacts:
                artifacts.append(path)
        return artifacts

    @staticmethod
    def _build_step_progress_item(state: AgentLoopState, step: PlanStep) -> Dict[str, str]:
        output_summary = FloodAgent._summarize_text_block(step.output_text)
        artifacts = FloodAgent._get_step_artifacts(state, step.step_id)
        return {
            "step_id": step.step_id,
            "title": step.title,
            "executor": step.executor,
            "purpose": step.purpose,
            "status": step.status,
            "output_summary": output_summary,
            "artifacts": json.dumps(artifacts, ensure_ascii=False) if artifacts else "[]",
        }

    def _verify_goal_state(self, state: AgentLoopState) -> Dict[str, Any]:
        payload = state.latest_payload or {}
        needs = self._needs_final_deliverable(state.original_input)
        artifacts = state.artifacts or self._collect_artifacts_from_payload(payload)
        artifacts_ok = self._artifacts_cover_needs(artifacts, needs)
        if not any(needs.values()):
            goal_satisfied = bool(str(payload.get("summary", "") or state.final_output or "").strip())
        else:
            goal_satisfied = artifacts_ok
        progress = self._classify_step_completion(state)
        reason = str((state.final_check or {}).get("final_goal", "") or "")
        if not goal_satisfied and not reason:
            if any(needs.values()) and not artifacts_ok:
                reason = "缺少与用户目标匹配的最终交付文件"
            else:
                reason = "用户最终目标尚未完成"
        return {
            "scope": "goal",
            "status": "pass" if goal_satisfied else "fail",
            "reason": reason,
            "goal_satisfied": goal_satisfied,
            "requires_replan": not goal_satisfied,
            "completed_steps": progress["completed_steps"],
            "pending_steps": progress["pending_steps"],
            "failed_step": progress["failed_step"],
            "reusable_artifacts": artifacts,
        }

    @staticmethod
    def _format_progress_lines(items: List[Dict[str, str]]) -> str:
        if not items:
            return "- 无"
        lines = []
        for item in items:
            title = str(item.get("title", "") or "未命名步骤")
            executor = str(item.get("executor", "") or "orchestrator")
            purpose = str(item.get("purpose", "") or "").strip()
            line = f"- {title} ({executor})"
            if purpose:
                line += f": {purpose}"
            output_summary = str(item.get("output_summary", "") or "").strip()
            artifacts = str(item.get("artifacts", "") or "").strip()
            if output_summary:
                line += f" | 产出摘要: {output_summary}"
            if artifacts and artifacts != "[]":
                line += f" | 产物: {artifacts}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _build_step_artifact_index(items: List[Dict[str, str]]) -> str:
        if not items:
            return "[]"

        index_payload: List[Dict[str, Any]] = []
        for item in items:
            raw_artifacts = str(item.get("artifacts", "") or "[]").strip() or "[]"
            try:
                artifacts = json.loads(raw_artifacts)
            except Exception:
                artifacts = [raw_artifacts] if raw_artifacts and raw_artifacts != "[]" else []

            index_payload.append({
                "step_id": str(item.get("step_id", "") or "").strip(),
                "title": str(item.get("title", "") or "").strip(),
                "executor": str(item.get("executor", "") or "").strip(),
                "status": str(item.get("status", "") or "").strip(),
                "purpose": str(item.get("purpose", "") or "").strip(),
                "output_summary": str(item.get("output_summary", "") or "").strip(),
                "artifacts": artifacts,
            })
        return json.dumps(index_payload, ensure_ascii=False, indent=2)

    def _run_final_file_existence_check(self, state: AgentLoopState) -> Dict[str, Any]:
        artifacts = list(dict.fromkeys(state.artifacts or self._collect_artifacts_from_payload(state.latest_payload)))
        if not artifacts:
            return {
                "overall_status": "pass",
                "is_final_goal_met": True,
                "final_goal": "",
                "completed_work": "无文件产物需要校验",
                "current_result_type": "unknown",
                "next_executor": "",
                "next_action": "",
            }

        existing_files: List[str] = []
        missing_files: List[str] = []
        for artifact in artifacts:
            try:
                tool_output = check_artifact_exists.invoke({"artifact_path": artifact, "scope": "current"})
            except Exception as exc:
                logger.warning("check_artifact_exists 调用失败，回退到 os.path.exists: %s", exc)
                tool_output = ""

            tool_text = str(tool_output or "").strip()
            if tool_text and "确认找到" in tool_text and "匹配产物" in tool_text:
                existing_files.append(artifact)
                continue

            if os.path.exists(str(artifact)):
                existing_files.append(artifact)
            else:
                missing_files.append(artifact)

        if not missing_files:
            return {
                "overall_status": "pass",
                "is_final_goal_met": True,
                "final_goal": "",
                "completed_work": f"已确认存在的文件: {', '.join(existing_files)}",
                "current_result_type": "final_deliverable",
                "next_executor": "",
                "next_action": "",
            }

        return {
            "overall_status": "fail",
            "is_final_goal_met": False,
            "final_goal": f"缺失文件: {', '.join(missing_files)}",
            "completed_work": f"已确认存在的文件: {', '.join(existing_files)}" if existing_files else "",
            "current_result_type": "unknown",
            "next_executor": "execution_specialist",
            "next_action": f"补齐缺失文件: {', '.join(missing_files)}",
        }

    def _build_progress_context(self, state: AgentLoopState, verification: Dict[str, Any]) -> str:
        completed_steps = verification.get("completed_steps") or []
        pending_steps = verification.get("pending_steps") or []
        failed_step = verification.get("failed_step") or {}
        reusable_artifacts = verification.get("reusable_artifacts") or state.artifacts or []
        latest_payload = state.latest_payload or {}
        step_artifact_index_items = completed_steps + pending_steps + ([failed_step] if failed_step else [])

        blocks = [
            "[已完成步骤]",
            self._format_progress_lines(completed_steps),
            "",
            "[未完成步骤]",
            self._format_progress_lines(pending_steps),
            "",
            "[当前失败/待补步骤]",
            self._format_progress_lines([failed_step] if failed_step else []),
            "",
            "[可复用产物]",
            json.dumps(reusable_artifacts, ensure_ascii=False, indent=2),
            "",
            "[前序步骤产物索引]",
            self._build_step_artifact_index(step_artifact_index_items),
        ]

        latest_summary = str(latest_payload.get("summary", "") or "").strip()
        if latest_summary:
            blocks.extend(["", "[最近一次结果摘要]", latest_summary])

        blocks.extend([
            "",
            "[执行约束]",
            "- 优先复用已完成步骤的中间结果和产物，不要默认从头重跑整条流程。",
            "- 如果只是最后一步失败，只续做最后一步；只有当现有中间结果不足以继续时，才允许回退到更上游步骤。",
        ])
        return "\n".join(blocks)

    def _get_runtime_state_dir(self) -> str:
        memory = getattr(self, "memory", None)
        memory_dir = getattr(memory, "memory_dir", None)
        if memory_dir:
            os.makedirs(str(memory_dir), exist_ok=True)
            return str(memory_dir)

        fallback_dir = os.path.join(os.getcwd(), "data", "agent_state")
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir

    def _build_artifact_record(self, path: str, producer_step: PlanStep) -> Dict[str, Any]:
        normalized = str(path or "").strip()
        lower_path = normalized.lower()
        artifact_type = "file"
        if lower_path.endswith((".xlsx", ".xls")):
            artifact_type = "spreadsheet"
        elif lower_path.endswith((".json",)):
            artifact_type = "json"
        elif lower_path.endswith((".csv", ".tsv")):
            artifact_type = "table"
        elif lower_path.endswith((".png", ".jpg", ".jpeg")):
            artifact_type = "image"
        elif lower_path.endswith((".docx", ".pdf")):
            artifact_type = "report"

        return {
            "path": normalized,
            "artifact_type": artifact_type,
            "producer_step_id": producer_step.step_id,
            "producer_executor": producer_step.executor,
            "deliverable_candidate": artifact_type in {"spreadsheet", "image", "report"},
            "validated": False,
            "updated_at": datetime.now().isoformat(),
        }

    def _register_artifacts(self, state: AgentLoopState, step: PlanStep, payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        new_records: List[Dict[str, Any]] = []
        for artifact in self._collect_artifacts_from_payload(payload):
            if artifact not in state.artifacts:
                state.artifacts.append(artifact)
            if any(record.get("path") == artifact for record in state.artifact_registry):
                continue
            record = self._build_artifact_record(artifact, step)
            state.artifact_registry.append(record)
            new_records.append(record)
        return new_records

    def _record_journal_entry(self, state: AgentLoopState, *, step: PlanStep, verification: Dict[str, Any], stage_input: str) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "step_id": step.step_id,
            "title": step.title,
            "executor": step.executor,
            "status": step.status,
            "attempt_count": step.attempt_count,
            "purpose": step.purpose,
            "input_text": stage_input,
            "output_text": step.output_text,
            "verification": verification,
        }
        state.execution_journal.append(entry)

    def _persist_loop_state(self, state: AgentLoopState) -> None:
        runtime_dir = self._get_runtime_state_dir()
        journal_path = os.path.join(runtime_dir, "execution_journal.json")
        artifact_path = os.path.join(runtime_dir, "artifact_registry.json")
        state_path = os.path.join(runtime_dir, "agent_loop_state.json")

        with open(journal_path, "w", encoding="utf-8") as handle:
            json.dump({"entries": state.execution_journal}, handle, ensure_ascii=False, indent=2)
        with open(artifact_path, "w", encoding="utf-8") as handle:
            json.dump({"artifacts": state.artifact_registry}, handle, ensure_ascii=False, indent=2)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "original_input": state.original_input,
                    "terminal_status": state.terminal_status,
                    "round_count": state.round_count,
                    "replan_count": state.replan_count,
                    "artifacts": state.artifacts,
                    "final_check": state.final_check,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    def _replan_after_verification(self, state: AgentLoopState, verification: Dict[str, Any]) -> None:
        payload = state.latest_payload or {}
        latest_stage = str(payload.get("stage", "") or "").strip()
        artifacts = self._collect_artifacts_from_payload(payload)
        needs = self._needs_final_deliverable(state.original_input)
        failed_step = verification.get("failed_step") or {}
        failed_executor = str(failed_step.get("executor", "") or "").strip()
        next_executor = self._normalize_executor_name(failed_executor) or "orchestrator"
        next_title = "根据校验反馈继续执行"

        if verification.get("scope") == "step" and failed_executor:
            next_title = f"继续完成：{failed_step.get('title', '当前失败步骤')}"
        elif verification.get("scope") == "goal":
            if needs["excel"] and not self._artifacts_cover_needs(artifacts or state.artifacts, {"excel": True, "image": False, "report": False}):
                next_executor = "execution_specialist"
                next_title = "基于已有结果补齐最终 Excel"
            elif failed_executor:
                next_executor = self._normalize_executor_name(failed_executor)
                next_title = f"继续完成：{failed_step.get('title', '当前失败步骤')}"

        if needs["excel"] and not self._artifacts_cover_needs(artifacts, {"excel": True, "image": False, "report": False}):
            if artifacts:
                next_executor = "execution_specialist"
                next_title = "基于中间结果生成最终 Excel"

        step_id = f"step-{len(state.plan_steps) + 1}"
        progress_context = self._build_progress_context(state, verification)
        reason_parts = []
        fallback_reason = str(verification.get("reason", "") or "根据校验反馈继续推进任务")
        reason_parts.append(fallback_reason)
        reason = "；".join(part for part in reason_parts if part)
        step = PlanStep(
            step_id=step_id,
            title=next_title,
            executor=next_executor,
            purpose=reason,
            input_text=(
                f"{suggested_action or next_title}\n\n"
                f"[最终目标摘要]\n{self._build_goal_brief(state.original_input)}\n\n"
                f"[当前待完成目标]\n{reason}\n\n"
                f"{progress_context}\n\n"
                "请只继续完成当前未完成步骤，禁止默认重跑已经完成的上游步骤。"
            ),
        )
        state.plan_steps.append(step)
        state.replan_count += 1

    def _run_agent_loop(self, user_input: str, callbacks: Optional[List[Any]] = None, event_sink: Optional[Any] = None) -> AgentLoopState:
        state = self._build_initial_loop_state(user_input)
        self._last_loop_state = state
        if event_sink:
            event_sink({
                "type": "plan_created",
                "steps": [
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "executor": step.executor,
                        "purpose": step.purpose,
                    }
                    for step in state.plan_steps
                ],
            })

        max_rounds = 6
        while state.round_count < max_rounds and state.terminal_status == "running":
            step = self._get_next_pending_step(state)
            if step is None:
                goal_verification = self._verify_goal_state(state)
                if goal_verification.get("goal_satisfied"):
                    final_file_check = self._run_final_file_existence_check(state)
                    state.final_check = final_file_check
                    if not final_file_check.get("is_final_goal_met"):
                        goal_verification = {
                            **goal_verification,
                            "status": "fail",
                            "reason": str(final_file_check.get("final_goal", "") or "涉及文件不存在"),
                            "goal_satisfied": False,
                            "requires_replan": True,
                            "final_check": final_file_check,
                        }
                        self._replan_after_verification(state, goal_verification)
                        continue
                    state.terminal_status = "completed"
                    self._persist_loop_state(state)
                    break
                self._replan_after_verification(state, goal_verification)
                continue

            step.status = "running"
            step.attempt_count += 1
            state.round_count += 1
            if event_sink:
                event_sink({
                    "type": "step_started",
                    "step_id": step.step_id,
                    "title": step.title,
                    "executor": step.executor,
                    "purpose": step.purpose,
                    "detail": step.purpose,
                })

            stage_input = self._build_step_input(state, step)
            executor_name = self._normalize_executor_name(step.executor)
            result = self._invoke_executor(self._executors[executor_name], stage_input, callbacks=callbacks, include_session_context=executor_name == "orchestrator")
            state.tool_calls.extend(self._record_intermediate_steps(result))
            if event_sink:
                for tool_event in self._build_tool_activity_events(result):
                    event_sink(tool_event)
            stage_output = (result.get("output", "") or "").strip()
            if executor_name != "orchestrator":
                stage_output = self._normalize_specialist_output(result, stage_output)

            payload = self._extract_result_payload(executor_name, result, stage_output)
            verification = self._verify_step_result(step, payload, stage_output)

            step.status = "completed" if verification.get("step_completed", verification.get("status") == "pass") else "failed"
            step.output_text = stage_output
            step.verification = verification
            state.previous_outputs.append((step.executor, stage_output))
            state.final_output = stage_output or state.final_output
            state.latest_payload = payload
            new_artifacts = self._register_artifacts(state, step, payload)
            self._record_journal_entry(state, step=step, verification=verification, stage_input=stage_input)
            self._persist_loop_state(state)
            self._last_loop_state = state

            if event_sink:
                for artifact in new_artifacts:
                    event_sink({"type": "artifact_created", "artifact": artifact})
                event_sink({
                    "type": "verification",
                    "step_id": step.step_id,
                    "title": step.title,
                    "verification": verification,
                })

            if event_sink:
                event_sink({
                    "type": "step_completed",
                    "step_id": step.step_id,
                    "title": step.title,
                    "executor": step.executor,
                    "status": step.status,
                    "detail": step.purpose,
                    "verification": verification,
                })

            goal_verification = self._verify_goal_state(state)
            if goal_verification.get("goal_satisfied"):
                final_file_check = self._run_final_file_existence_check(state)
                state.final_check = final_file_check
                if not final_file_check.get("is_final_goal_met"):
                    goal_verification = {
                        **goal_verification,
                        "status": "fail",
                        "reason": str(final_file_check.get("final_goal", "") or "涉及文件不存在"),
                        "goal_satisfied": False,
                        "requires_replan": True,
                        "final_check": final_file_check,
                    }
                else:
                    goal_verification["final_check"] = final_file_check

            if goal_verification.get("goal_satisfied"):
                state.terminal_status = "completed"
                state.final_check = goal_verification.get("final_check") or state.final_check
                self._persist_loop_state(state)
                if event_sink:
                    event_sink({"type": "goal_completed", "verification": goal_verification})
                break

            if verification.get("requires_replan") or goal_verification.get("requires_replan"):
                self._replan_after_verification(state, verification if verification.get("requires_replan") else goal_verification)
                if event_sink:
                    last_step = state.plan_steps[-1]
                    event_sink({
                        "type": "replan",
                        "reason": str((verification if verification.get("requires_replan") else goal_verification).get("reason", "") or "用户最终目标尚未完成"),
                        "next_step": {"step_id": last_step.step_id, "title": last_step.title, "executor": last_step.executor},
                        "steps": [
                            {
                                "step_id": plan_step.step_id,
                                "title": plan_step.title,
                                "executor": plan_step.executor,
                                "purpose": plan_step.purpose,
                                "status": plan_step.status,
                            }
                            for plan_step in state.plan_steps
                        ],
                    })

        if state.terminal_status == "running":
            state.terminal_status = "budget_exceeded"
            if state.latest_payload:
                state.final_output = self._build_forced_continuation_input(user_input, state.latest_payload)
            self._persist_loop_state(state)
        self._last_loop_state = state
        return state

    def _build_stage_user_input(self, stage_name: str, original_input: str, previous_outputs: List[Tuple[str, str]]) -> str:
        stage_name = self._normalize_executor_name(stage_name)
        if not previous_outputs:
            return original_input

        context_blocks = [f"[{name}]\n{output}" for name, output in previous_outputs if output]
        return (
            f"[原始用户需求]\n{original_input}\n\n"
            f"[已有中间结果]\n" + "\n\n".join(context_blocks)
        )

    def _invoke_executor(self, executor: AgentExecutor, user_input: str, callbacks: Optional[List[Any]] = None, include_session_context: bool = True) -> Dict[str, Any]:
        agent_input = {
            "input": user_input,
            "current_time_context": self._get_current_time_context(),
            "current_system_context": self._get_current_system_context(),
        }

        if include_session_context:
            agent_input["chat_history"] = self._get_chat_history_messages()
            context_messages = self._build_context_messages()
            if context_messages:
                agent_input["long_term_memory"] = context_messages

        reset_retry_guard()
        if callbacks:
            return executor.invoke(agent_input, config={"callbacks": callbacks})
        return executor.invoke(agent_input)

    def _record_intermediate_steps(self, result: Dict[str, Any]) -> List[Dict[str, str]]:
        recorded_tool_calls: List[Dict[str, str]] = []
        for step in result.get("intermediate_steps", []):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue
            action, observation = step[0], step[1]
            tool_name = getattr(action, 'tool', '')
            tool_input = getattr(action, 'tool_input', '')
            observation_text = str(observation)
            self._remember_tool_use(tool_name=tool_name, tool_input=str(tool_input), tool_output=observation_text)
            if tool_name:
                recorded_tool_calls.append({
                    "tool_name": str(tool_name),
                    "tool_input": str(tool_input),
                    "tool_output": observation_text,
                })
            if self._is_structured_tool_result(tool_name, observation_text):
                self._remember_reusable_result(observation_text.strip())
        return recorded_tool_calls

    @staticmethod
    def _build_tool_activity_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for step in result.get("intermediate_steps", []):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue
            action, observation = step[0], step[1]
            tool_name = str(getattr(action, "tool", "") or "").strip()
            if not tool_name:
                continue

            tool_input = str(getattr(action, "tool_input", "") or "").strip()
            observation_text = str(observation or "").strip()
            status_event: Dict[str, Any] = {
                "type": "tool_status",
                "tool_name": tool_name,
                "status": "running",
            }
            if tool_input:
                status_event["content"] = tool_input
            events.append(status_event)

            if observation_text:
                events.append({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "content": observation_text,
                })
        return events

    def _build_fallback_assistant_output(self, tool_calls: List[Dict[str, str]]) -> str:
        recent_result = self.memory.get_recent_reusable_result() if hasattr(self.memory, 'get_recent_reusable_result') else ""
        if recent_result and recent_result.strip():
            return recent_result.strip()

        last_tool_use = self.memory.get_last_tool_use() if hasattr(self.memory, 'get_last_tool_use') else {}
        tool_name = str(last_tool_use.get("tool_name", "") or "").strip()
        tool_output = str(last_tool_use.get("tool_output", "") or "").strip()
        if tool_name and tool_output:
            return f"任务已执行完成。\n\n最近工具结果（{tool_name}）：\n{tool_output}"

        if tool_calls:
            last_call = tool_calls[-1]
            tool_name = str(last_call.get("tool_name", "") or "").strip()
            tool_output = str(last_call.get("tool_output", "") or "").strip()
            if tool_name and tool_output:
                return f"任务已执行完成。\n\n最近工具结果（{tool_name}）：\n{tool_output}"

        return ""

    def _run_execution_plan(self, user_input: str) -> Tuple[str, List[Tuple[str, str]], List[Dict[str, str]]]:
        plan = self._select_execution_plan(user_input)
        logger.info(f"执行计划: {plan}")

        previous_outputs: List[Tuple[str, str]] = []
        tool_calls: List[Dict[str, str]] = []
        final_output = "抱歉，我无法回答这个问题。"

        for stage_name in plan:
            executor = self._executors[stage_name]
            stage_input = self._build_stage_user_input(stage_name, user_input, previous_outputs)
            stage_output = ""

            for forced_round in range(3):
                result = self._invoke_executor(executor, stage_input)
                tool_calls.extend(self._record_intermediate_steps(result))
                stage_output = result.get("output", "").strip()

                latest_delegation = self._extract_latest_delegation_payload(result)
                should_continue = stage_name == "orchestrator" and self._should_force_continue(latest_delegation)
                feedback_message = self._build_validation_feedback_message(latest_delegation, should_continue)
                if feedback_message:
                    logger.info(feedback_message.strip())

                if not should_continue:
                    break

                logger.warning("检测到中间结果未满足最终目标，强制主 agent 继续执行下一步")
                stage_input = self._build_forced_continuation_input(user_input, latest_delegation)

            if stage_output:
                previous_outputs.append((stage_name, stage_output))
                final_output = stage_output

        return final_output, previous_outputs, tool_calls

    @staticmethod
    def _extract_artifact_paths(text: str) -> List[str]:
        candidates = re.findall(
            r"[A-Za-z]:(?:\\|/)[^\s\"\*]+\.(?:json|csv|xlsx|xls|docx|pdf|png|jpg|jpeg)|/[^\s\"\*]+\.(?:json|csv|xlsx|xls|docx|pdf|png|jpg|jpeg)",
            text or "",
            flags=re.IGNORECASE,
        )
        seen = []
        for item in candidates:
            if item not in seen:
                seen.append(item)
        return seen

    @staticmethod
    def _extract_latest_delegation_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for step in reversed(result.get("intermediate_steps", [])):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue
            action, observation = step[0], step[1]
            tool_name = getattr(action, "tool", "")
            if tool_name != "delegate_execution_specialist":
                continue
            text = str(observation or "").strip()
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        return None

    @staticmethod
    def _should_force_continue(payload: Optional[Dict[str, Any]]) -> bool:
        if not payload or payload.get("result_type") != "intermediate":
            return False

        if FloodAgent._artifacts_satisfy_user_goal(payload):
            return False
        return False

    @staticmethod
    def _artifacts_satisfy_user_goal(payload: Optional[Dict[str, Any]]) -> bool:
        if not payload:
            return False
        artifacts = payload.get("artifacts") or []
        if not artifacts:
            return False

        user_input = str(payload.get("user_goal") or payload.get("task") or "")
        # task/summary 不够时，用主 agent 构造 forced input 补足，所以这里只做偏保守判断。
        # 真正的用户需求由 _build_forced_continuation_input 继续携带，不在这里做过宽推断。
        lower_text = user_input.lower()
        lower_artifacts = [str(path).lower() for path in artifacts]

        needs_excel = any(marker in lower_text for marker in ("excel", ".xlsx", "工作表", "结果表", "导出表"))
        needs_image = any(marker in lower_text for marker in ("图片", "图", "过程线", "plot", ".png", ".jpg", ".jpeg"))
        needs_report = any(marker in lower_text for marker in ("报告", "docx", "word", ".docx", ".pdf", "pdf"))

        has_excel = any(path.endswith(".xlsx") or path.endswith(".xls") for path in lower_artifacts)
        has_image = any(path.endswith(".png") or path.endswith(".jpg") or path.endswith(".jpeg") for path in lower_artifacts)
        has_report = any(path.endswith(".docx") or path.endswith(".pdf") for path in lower_artifacts)

        if needs_excel and has_excel:
            return True
        if needs_image and has_image:
            return True
        if needs_report and has_report:
            return True
        return False

    @staticmethod
    def _build_forced_continuation_input(original_user_input: str, payload: Dict[str, Any]) -> str:
        return (
            f"[最终目标摘要]\n{FloodAgent._build_goal_brief(original_user_input)}\n\n"
            "[系统强制继续结论]\n"
            "最近一次结果仍未满足用户最终目标。你现在不得结束，必须继续执行后续步骤。\n\n"
            f"[最近一次中间结果]\n{payload.get('summary', '')}\n\n"
            "请根据当前已有结果继续调用合适的技能、工具或执行单元，直到真正完成用户最终目标。"
        )

    @staticmethod
    def _build_validation_feedback_message(payload: Optional[Dict[str, Any]], should_continue: bool) -> str:
        if not payload:
            return ""

        lines = ["[调度检查]", f"- has_artifacts: {bool((payload or {}).get('artifacts'))}"]

        if should_continue:
            lines.append("- 结论: 当前结果仍视为中间结果，主 agent 需要继续执行后续步骤。")
        else:
            lines.append("- 结论: 当前结果已可接受，无需强制继续。")

        return "\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _is_agent_stopped_message(text: str) -> bool:
        normalized = (text or "").strip().lower()
        return "agent stopped due to iteration limit or time limit." in normalized

    @staticmethod
    def _looks_like_successful_artifact_output(tool_name: str, content: str) -> bool:
        if tool_name not in {"run_script", "exec_bash", "exec_python_file", "write_text_file"}:
            return False

        text = (content or "").strip()
        if not text:
            return False

        success_markers = (
            "已生成",
            "生成成功",
            "输出文件路径",
            "写入成功",
            "保存到",
            "文件已创建",
            "文件已写入",
            "执行成功",
        )
        failure_markers = (
            "错误：",
            "执行失败",
            "执行超时",
            "命令执行失败",
            "python 文件执行失败",
            "traceback",
        )

        lower_text = text.lower()
        if any(marker.lower() in lower_text for marker in failure_markers):
            return False
        return any(marker in text for marker in success_markers)

    def _extract_specialist_fallback_output(self, result: Dict[str, Any]) -> str:
        for step in reversed(result.get("intermediate_steps", [])):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue
            action, observation = step[0], step[1]
            tool_name = getattr(action, "tool", "")
            observation_text = str(observation or "").strip()
            if self._looks_like_successful_artifact_output(tool_name, observation_text):
                return observation_text
        return ""

    def _normalize_specialist_output(self, result: Dict[str, Any], output: str) -> str:
        text = (output or "").strip()
        if not text:
            return self._extract_specialist_fallback_output(result)

        if self._is_agent_stopped_message(text):
            fallback = self._extract_specialist_fallback_output(result)
            if fallback:
                logger.warning("执行单元在停止前已产出有效结果，改用最近一次成功工具输出作为返回内容")
                return fallback
            return "执行单元达到迭代次数或执行时间上限，且未能整理出最终子任务结论。"

        if "Agent stopped due to iteration limit or time limit." in text:
            fallback = self._extract_specialist_fallback_output(result)
            if fallback:
                logger.warning("执行单元输出中包含停止提示，已替换为最近一次成功工具输出")
                return fallback
            return text.replace(
                "Agent stopped due to iteration limit or time limit.",
                "执行单元达到迭代次数或执行时间上限。",
            ).strip()

        return text

    @staticmethod
    def _is_structured_tool_result(tool_name: str, content: str) -> bool:
        if tool_name not in {"run_script", "exec_bash"}:
            return False
        text = (content or "").strip()
        if not text:
            return False
        has_heading = text.startswith("## ") or "\n## " in text
        has_markdown_table = "|------" in text or "|-----" in text
        return has_heading and has_markdown_table

    def _remember_reusable_result(self, content: str):
        if hasattr(self.memory, 'set_recent_reusable_result') and content:
            self.memory.set_recent_reusable_result(content)

    def _remember_tool_use(self, tool_name: str, tool_input: str = "", tool_output: str = ""):
        if hasattr(self.memory, 'set_last_tool_use') and tool_name:
            self.memory.set_last_tool_use(tool_name=tool_name, tool_input=tool_input, tool_output=tool_output)

    def _get_recent_result_message(self) -> Optional[SystemMessage]:
        recent_result = self.memory.get_recent_reusable_result() if hasattr(self.memory, 'get_recent_reusable_result') else ""
        if recent_result:
            return SystemMessage(
                content=(
                    "[最近一次可复用结果]\n"
                    "以下内容来自当前会话最近一次已完成的分析/预测/统计结果。"
                    "如果用户当前任务是继续加工、生成文档、绘图或整理汇总，优先直接复用这份结果，"
                    "不要默认重新运行上游分析。\n\n"
                    f"{recent_result}"
                )
            )
        return None

    def _get_last_tool_use_message(self) -> Optional[SystemMessage]:
        last_tool_use = self.memory.get_last_tool_use() if hasattr(self.memory, 'get_last_tool_use') else {}
        tool_name = (last_tool_use or {}).get("tool_name", "").strip()
        if not tool_name:
            return None

        tool_input = (last_tool_use.get("tool_input", "") or "").strip()
        tool_output = (last_tool_use.get("tool_output", "") or "").strip()
        sections = [
            "[最近一次工具执行记录]",
            f"工具名: {tool_name}",
        ]
        if tool_input:
            sections.append(f"工具输入:\n{tool_input}")
        if tool_output:
            sections.append(f"工具输出:\n{tool_output}")
        sections.append("如果用户当前任务是在上一轮执行结果基础上继续，请优先复用这次工具执行记录，而不是默认重跑。")
        return SystemMessage(content="\n\n".join(sections))

    def run(self, user_input: str) -> str:
        """
        运行智能体，处理用户输入

        Args:
            user_input: 用户输入的问题或指令

        Returns:
            智能体的响应
        """
        try:
            logger.info(f"收到用户输入: {user_input[:50]}...")
            self._active_user_input = user_input

            active_notice = None
            if hasattr(self.memory, 'add_user_message'):
                result = self.memory.add_user_message(user_input)
                if result:
                    active_notice = result

            loop_state = self._run_agent_loop(user_input)
            output = loop_state.final_output
            tool_calls = loop_state.tool_calls

            if hasattr(self.memory, 'add_ai_message_with_trace'):
                self.memory.add_ai_message_with_trace(output, tool_calls=tool_calls)
            elif hasattr(self.memory, 'add_ai_message'):
                self.memory.add_ai_message(output)

            if hasattr(self.memory, 'save_conversation'):
                self.memory.save_conversation(user_input, output)

            if active_notice:
                output = f"【{active_notice}】\n\n{output}"

            logger.info("智能体执行成功")
            return output

        except Exception as e:
            error_msg = f"智能体执行失败: {str(e)}"
            logger.error(error_msg)
            return f"抱歉，处理您的请求时出错了：{str(e)}"
        finally:
            self._active_user_input = ""

    def get_memory_summary(self) -> Dict[str, Any]:
        """获取记忆摘要"""
        return self.memory.to_dict()

    def clear_memory(self):
        """清空记忆"""
        self.memory.clear()
        logger.info("智能体记忆已清空")

    def chat(self, message: str) -> str:
        """对话接口（run方法的别名）"""
        return self.run(message)

    def stream(self, user_input: str, enable_reasoning: bool = False):
        """
        流式运行智能体（token 级别），通过 Queue + threading 实现真实流式

        Yields:
            dict: {"type": "token"/"tool_call"/"tool_result"/"error"/"reasoning", "content": str}
        """
        try:
            logger.info(f"收到用户输入(流式): {user_input[:50]}...")
            self._active_user_input = user_input

            if hasattr(self.memory, 'set_status_callback'):
                self.memory.set_status_callback(None)

            active_notice = None
            if hasattr(self.memory, 'add_user_message'):
                result = self.memory.add_user_message(user_input)
                if result:
                    active_notice = result
                    yield {"type": "system", "content": f"【{result}】"}

            full_answer = ""
            full_reasoning = ""
            full_tool_calls: List[Dict[str, str]] = []
            q: queue.Queue = queue.Queue()
            result_holder: Dict[str, Any] = {}
            last_summary_text = ""
            saw_native_reasoning = False

            def normalize_summary_text(text: str) -> str:
                normalized = re.sub(r'\s+', ' ', str(text or '')).strip()
                return normalized

            def emit_summary_event(text: str) -> Optional[Dict[str, Any]]:
                nonlocal full_reasoning, last_summary_text
                normalized = normalize_summary_text(text)
                if not normalized or normalized == last_summary_text:
                    return None
                last_summary_text = normalized
                full_reasoning += ("\n\n" if full_reasoning else "") + normalized
                return {"type": "thought_summary", "content": normalized}

            def expand_text_for_thoughts(text: str, limit: int = 1200) -> str:
                raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                if not raw:
                    return ""
                raw = re.sub(r'\n{3,}', '\n\n', raw)
                raw = re.sub(r'[ \t]+', ' ', raw)
                raw = re.sub(r' *\n *', '\n', raw).strip()
                if len(raw) > limit:
                    raw = raw[:limit].rstrip() + "\n..."
                return raw

            def summarize_tool_status(tool_name: str, content: str = "", status: str = "running") -> str:
                tool_name = str(tool_name or "").strip()
                content = expand_text_for_thoughts(content, limit=300)
                if status == "error":
                    if content:
                        return f"工具 `{tool_name or 'unknown'}` 执行失败：\n{content}"
                    return f"工具 `{tool_name or 'unknown'}` 执行失败。"

                if content:
                    return f"开始调用工具 `{tool_name or 'unknown'}`：\n{content}"
                return f"开始调用工具 `{tool_name or 'unknown'}`。"

            def summarize_tool_result(tool_name: str, content: str = "") -> str:
                tool_name = str(tool_name or "").strip()
                text = expand_text_for_thoughts(content, limit=1600)
                if not text:
                    return f"工具 `{tool_name or 'unknown'}` 执行完成。"
                return f"工具 `{tool_name or 'unknown'}` 返回：\n{text}"

            def build_step_summary(event: Dict[str, Any]) -> str:
                detail = normalize_summary_text(event.get("detail", "") or event.get("purpose", ""))
                title = normalize_summary_text(event.get("title", "") or "当前步骤")
                if detail:
                    return detail
                return f"开始处理：{title}。"

            def normalize_loop_events(event: Dict[str, Any]) -> List[Dict[str, Any]]:
                nonlocal full_reasoning
                event_type = event.get("type")
                if event_type == "plan_created":
                    steps = event.get("steps", []) or []
                    return [{
                        "type": "workflow_plan",
                        "title": "Agent Loop",
                        "steps": [
                            {
                                "key": step.get("step_id", ""),
                                "label": step.get("executor", "orchestrator"),
                                "title": step.get("title", "待执行"),
                                "detail": step.get("detail", "") or step.get("purpose", ""),
                                "status": "pending",
                            }
                            for step in steps
                        ],
                    }]
                if event_type == "step_started":
                    events: List[Dict[str, Any]] = []
                    events.append({
                        "type": "workflow_step",
                        "step_key": event.get("step_id", ""),
                        "label": event.get("executor", "orchestrator"),
                        "title": event.get("title", "待执行"),
                        "detail": event.get("detail", "") or event.get("purpose", ""),
                        "status": "running",
                    })
                    return events
                if event_type == "step_completed":
                    verification = event.get("verification", {}) or {}
                    return [{
                        "type": "workflow_step",
                        "step_key": event.get("step_id", ""),
                        "label": event.get("executor", "orchestrator"),
                        "title": event.get("title", "待执行"),
                        "detail": event.get("detail", "") or event.get("purpose", ""),
                        "status": "completed" if event.get("status") == "completed" else "failed",
                        "outcome": str(verification.get("reason", "") or "").strip(),
                    }]
                if event_type == "replan":
                    steps = event.get("steps", []) or []
                    next_step = event.get("next_step", {}) or {}
                    events: List[Dict[str, Any]] = [{
                        "type": "workflow_plan",
                        "title": "Agent Loop",
                        "steps": [
                            {
                                "key": step.get("step_id", ""),
                                "label": step.get("executor", "orchestrator"),
                                "title": step.get("title", "待执行"),
                                "detail": step.get("detail", "") or step.get("purpose", ""),
                                "status": "completed" if step.get("status") == "completed" else ("failed" if step.get("status") == "failed" else "pending"),
                            }
                            for step in steps
                        ],
                    }]
                    if next_step.get("step_id"):
                        events.append({
                            "type": "workflow_step",
                            "step_key": next_step.get("step_id", ""),
                            "label": next_step.get("executor", "orchestrator"),
                            "title": next_step.get("title", "待执行"),
                            "detail": str(event.get("reason", "") or ""),
                            "status": "pending",
                        })
                    return events
                if event_type == "artifact_created":
                    return []
                if event_type == "verification":
                    return []
                if event_type == "goal_completed":
                    return []
                return [event]

            def _run_loop() -> None:
                try:
                    callback = _FunctionsStreamCallback(q, enable_reasoning=enable_reasoning)
                    loop_state = self._run_agent_loop(
                        user_input,
                        callbacks=[callback],
                        event_sink=lambda event: [q.put(("event", item)) for item in normalize_loop_events(event)],
                    )
                    result_holder["state"] = loop_state
                except Exception as exc:
                    q.put(("error", str(exc)))
                finally:
                    q.put(("__done__", ""))

            t = threading.Thread(target=_run_loop, daemon=True)
            t.start()

            while True:
                event_type, content = q.get()
                if event_type == "__done__":
                    break
                if event_type == "error":
                    raise RuntimeError(content)
                if event_type in {"reasoning", "token", "search_result"}:
                    if event_type == "reasoning" and str(content or "").strip():
                        saw_native_reasoning = True
                    yield {"type": event_type, "content": str(content or "")}
                    continue
                if event_type in {"tool_status", "tool_result"}:
                    payload = content if isinstance(content, dict) else {"content": str(content or "")}
                    payload["type"] = event_type
                    yield payload
                    continue
                if event_type == "event":
                    event = content if isinstance(content, dict) else {"type": "reasoning", "content": str(content)}
                    if event.get("type") == "thought_summary" and saw_native_reasoning:
                        continue
                    yield event

            loop_state = result_holder.get("state")
            if loop_state is None:
                raise RuntimeError("Agent loop did not return a state")

            full_tool_calls.extend(loop_state.tool_calls)
            full_answer = loop_state.final_output
            if full_answer:
                yield {"type": "token", "content": full_answer}

            if full_answer:
                if self._is_structured_tool_result("run_script", full_answer):
                    self._remember_reusable_result(full_answer)
                if hasattr(self.memory, 'add_ai_message_with_trace'):
                    self.memory.add_ai_message_with_trace(full_answer, full_reasoning, full_tool_calls)
                elif hasattr(self.memory, 'add_ai_message_with_reasoning'):
                    self.memory.add_ai_message_with_reasoning(full_answer, full_reasoning)
                elif hasattr(self.memory, 'add_ai_message'):
                    self.memory.add_ai_message(full_answer)
            elif full_reasoning or full_tool_calls:
                fallback_answer = self._build_fallback_assistant_output(full_tool_calls)
                if fallback_answer:
                    full_answer = fallback_answer
                    if hasattr(self.memory, 'add_ai_message_with_trace'):
                        self.memory.add_ai_message_with_trace(full_answer, full_reasoning, full_tool_calls)
                    elif hasattr(self.memory, 'add_ai_message_with_reasoning'):
                        self.memory.add_ai_message_with_reasoning(full_answer, full_reasoning)
                    elif hasattr(self.memory, 'add_ai_message'):
                        self.memory.add_ai_message(full_answer)
            if hasattr(self.memory, 'save_conversation'):
                self.memory.save_conversation(user_input, full_answer)
            if hasattr(self.memory, 'set_status_callback'):
                self.memory.set_status_callback(None)
            logger.info("智能体流式执行成功")

        except Exception as e:
            error_msg = f"智能体流式执行失败: {str(e)}"
            logger.error(error_msg)
            if hasattr(self.memory, 'set_status_callback'):
                self.memory.set_status_callback(None)
            yield {"type": "reasoning", "content": f"抱歉，处理您的请求时出错了：{str(e)}"}
        finally:
            self._active_user_input = ""

    def chat_stream(self, message: str):
        """流式对话接口（stream方法的别名）"""
        yield from self.stream(message)
