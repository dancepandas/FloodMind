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
from tools import get_skill, run_script, exec_bash, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, read_artifact, knowledge_search, add_knowledge, web_search, add_memory, search_memory, set_rag_config, set_memory_instance, reset_retry_guard
from config.settings import settings

logger = logging.getLogger(__name__)


class SpecialistTaskInput(BaseModel):
    task: str = Field(default="", description="交给子 agent 的明确子任务说明，应尽量具体、短小、可执行")


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
    final_verification: Dict[str, Any] = field(default_factory=dict)
    round_count: int = 0
    replan_count: int = 0
    terminal_status: str = "running"
    execution_journal: List[Dict[str, Any]] = field(default_factory=list)
    artifact_registry: List[Dict[str, Any]] = field(default_factory=list)


class FloodAgent:
    """洪水预报智能体类 - 使用 OpenAI Functions Agent"""

    SYSTEM_PROMPT = """你是大水云开发的智能体，
    只负责：
    1. 理解用户需求
    2. 规划任务
    3. 处理简单的初始任务
    4. 给各子分发具体任务
    5. 总结回答用户
    

## 当前系统时间
{current_time_context}

## 可用工具(tools)
以下工具用于执行skill和系统操作：
- get_skill: 获取skill的详细说明和使用方法
- run_script: 执行skill中的Python脚本
- exec_bash: 通过Windows PowerShell执行Shell命令，可用来获取系统状态、写临时脚本或执行系统命令（非必要不使用）
- exec_python_file: 执行本地Python文件，适合运行临时 `.py` 脚本或复杂文件转换逻辑
- write_text_file: 直接写入文本文件，适合生成临时 `.py`、`.json`、`.csv` 文件
- search_tool_error_memory: 搜索全局工具错误记忆库，查看以前遇到过的 tool/skill/脚本错误及其输入字段和错误摘要
  - search_artifacts: 搜索当前会话或历史可复用 Python 脚本
  - read_artifact: 读取 `.py`、`.md`、`.txt`、`.json` 文本文件
- knowledge_search: 从知识库检索相关资料
- add_knowledge: 将文档添加到知识库
- web_search: 网络搜索，获取实时信息、最新新闻、网络资料
- add_memory: 将重要内容添加到长期记忆
- search_memory: 在对话历史和Skills文档中搜索关键词，支持正则表达式

## 可用分配和使用技能(skills)
{skill_catalog}

## 可用子 agent 介绍
- `delegate_python_specialist`：用于所有需要编写临时py脚本处理的任务，如数据提取、日志解析、中间 JSON/CSV构造、绘图等任务。
    你可以这样指派任务：我现在需要编写一个脚本将data/sessions/session-1776160969264-1adzerkfk/outputs/hydro_input.xlsx文件转换为格式为....形式的json文件
- `delegate_excel_specialist`：用于除去表格数据预览以外的所有复杂Excel处理任务，你需要明确告诉它你传入的数据文件结果如行数、列名等以及你的最终目的。
    你可以这样指派任务：请把data/sessions/session-1776160969264-1adzerkfk/outputs/result.json文件按照stationCdoe字段转换为标准的excel表格
- `delegate_validator`：用于对照用户需求，校验结果是否满足要求，你需要告诉它你生成了哪些中间文件、最近一次结果文件、传入接口的文件等文件的路径。
    你可以这样指派任务：请检查data/sessions/session-1776160969264-1adzerkfk/outputs/input.json文件中的内容是否符合skills/aojiang-hydro的输入要求

## 敖江流域子任务编码
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~水动力模型区间断面预报`、`水动力模型区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`
- 详细信息查看 aojiang-hydro SKILL.md文档

## 工作流程
1. 分析用户意图，明确用户最终目标
**注意：如果发现有与用户最终目标相关的skill，无论现阶段是否必要，都必须使用get_skill工具优先获取对应skill更详细的信息。**
2. 根据用户意图和目标先找到相应子agent、skill、tool，如果有对应的skill时先用get_skill工具获取更详细的信息
3. 区分用户是上传了数据文件如excel、csv等文件还是用户用自然语言描述降雨、流量等情况；
### 用户上传数据文件流程
1. 如果用户上传了数据文件，根据get_skill工具获取的详细的信息对数据进行分析、重组、转换等操作
2. 根据get_skill工具获取的详细的信息，按照引导完成任务
3. 使用`delegate_validator`验证输出成果质量
4. 整理处理好的正确文件路径（如excel、word、csv等）和任务结果总结
### 用户自然语言描述流程
1. 如果用户是进行一些对话任务而不是专业的执行任务，则根据对话内容自行调用相应工具、skill等进行回答即可
2. 如果用户是用自然语言描述降雨、流量等情况，先将自然语言其转换为结构化的数据如excel、csv等，再走用户上传数据文件流程


## 长期记忆
当用户明确要求记住某事，或识别到以下重要信息时，使用 add_memory 工具记录：
- 用户偏好：用户明确表达的喜好或习惯
- 重要决策：双方确认的重要决定
- 重要规则：用户强调的禁止或必须事项
- 关键信息：用户希望长期保留的内容

## 信息检索策略
当需要查找之前对话中的具体内容时，使用 search_memory 工具：
- 用户显式要求搜索时："帮我找一下之前关于xxx的对话"
- 模型判断上下文不够时，自动调用 search_memory 进行针对性搜索
- 搜索范围包括完整对话历史和Skills文档

## 工作原则
1. 工具调用失败后，必须分析错误原因并修正参数后重试，禁止直接放弃
2. 工具返回错误信息时，禁止编造或假设数据，必须按照错误提示的步骤重新调用工具
3. 当遇到重复的 tool/skill/脚本错误，或怀疑以前已经踩过类似问题时，可调用 `search_tool_error_memory` 搜索历史错误原因，避免重复走弯路
4. 如果生成文件有错，优先选择修改原文件或者覆盖原文件，非必要不制造新文件

## **绝对红线**
**用户发出质疑后，必须根据用户问题重新按照完整流程执行任务！**
**具体任务委派给子Agent执行，你复杂把握整体规划与结果质量！**
**严禁把超长json块直接放进工具参数，必须先调用`delegate_python_specialist`生成标准json文件，再把文件路径送进工具参数！**
**必须严格遵循SKILL.md及其相关文档的说明！**

## 输出规范
- 最终输出不要包含任何系统路径（如 D:\\...\\）
- 最终输出不要包含"输出目录"、"上传目录"等环境信息
- 最终输出不要包含"[会话环境信息]"等系统内部信息
- 最终输出只返回不包含会话id的文件名，不返回完整路径
- 最终输出为标准的Markdown格式
- 最终输出不要提及tools和{skill_catalog}中未包含的能力
- 最终输出**必须包含工具返回的完整结果**
"""

    PYTHON_SPECIALIST_PROMPT = """你是 Python Specialist 子 agent，只负责数据提取、清洗、转换、脚本生成和中间文件构建。

## 核心职责
1. 优先从原始文件读取数据，不要根据预览文本手工重建整份长数据
2. 当需要复杂转换、日志解析、JSON 生成、input.json 构造时，优先先搜索当前会话或历史可复用 Python 脚本；若找不到合适脚本，再使用 `write_text_file` 生成临时 `.py` 文件
3. 生成临时 `.py` 文件后，优先使用 `exec_python_file` 执行
4. 必要时可以调用 `get_skill` 和 `run_script`，但不得猜测 skill 未声明的参数、脚本或能力
5. 当用户要把日志、tool_result、模型结果或文本结果导出为 Excel 时，你的职责是先提取成中间结构化文件，再交给 Excel Specialist；不要直接试图一次性生成最终 Excel
6. 一旦你已经生成了本次子任务要求的目标中间文件（如 `input.json`、`.json`、`.csv`、临时 `.py`），必须立即结束并返回结果，不要继续规划下游步骤，不要继续重复检查，也不要因为“还能继续优化”而继续调用工具

## 强约束
- 禁止把超长 JSON 直接塞进工具参数
- 禁止优先使用超长 `python -c` 单行命令
- 禁止根据日志预览文本或聊天文本手工搬运大时序数组
- 如果已经生成目标文件，禁止继续无目的地重复调用工具；应立刻给出最终子任务结果
- 输出要明确说明生成了什么中间文件、包含什么结构、下一步怎么用
- 当前环境支持 Windows PowerShell；不要输出“PowerShell 不可用”之类判断

## 工具使用偏好
1. `search_artifacts`
2. `read_artifact`
3. `write_text_file`
4. `exec_python_file`
5. `get_skill`
6. `run_script`
7. `exec_bash`（仅限简单 shell 语句，不用于复杂写文件）

## 输出要求
- 面向调度器输出简洁结果
- 如果生成了中间文件，要说明文件作用
- 如果需求涉及下游 Excel/模型调用，明确给出建议的下游输入文件
- 对于日志转 Excel 场景，优先输出 `.json` 或 `.csv` 中间文件，并说明建议的 sheet 拆分维度（如按断面、按模型、按站点）
"""

    EXCEL_SPECIALIST_PROMPT = """你是 Excel Specialist 子 agent，只负责 Excel/CSV/TSV 相关的结构设计、导出和整理。

## 核心职责
1. 先思考 workbook 结构，再决定如何生成
2. 多对象、多断面、多时间序列数据，优先考虑 `Summary` + 每对象一个工作表
3. 优先使用 `xlsx` skill；如果 skill 现有脚本不足以完成需求，优先先搜索历史可复用 Python 脚本；仍不足时再生成临时 Python 脚本来创建多工作表 Excel
4. 如果输入是日志、JSON 或其他非结构化数据，需要先生成中间结构化文件，再导出 Excel
5. 当任务是“按断面/按对象分 sheet”时，默认先考虑：`Summary` 总览 + 每断面一个 sheet，而不是把所有内容塞进单表

## 强约束
- 禁止猜测 `xlsx` skill 未声明的脚本或参数
- 禁止把大表数据直接塞进 `run_script` 或 `write_text_file` 的长 JSON 参数
- 如需生成大量表格内容，优先先写临时 Python 脚本，再执行该脚本生成 Excel

## 推荐工作流
1. `get_skill('xlsx')` 确认可用能力
2. 若现有脚本足够，优先 `run_script`
3. 若需要多工作表或复杂结构，优先 `search_artifacts` / `read_artifact` 查找历史可复用 `.py` 脚本模板
4. 若仍无合适模板，再使用 `write_text_file` + `exec_python_file`
5. 生成 Excel 后，输出文件结构说明（例如 Summary + 各断面 sheet）
6. 如果上游已经产出中间 `.json`/`.csv` 文件，优先消费该中间文件，不要让调度器把大数组直接塞给你

## 输出要求
- 说明最终 Excel 的表结构
- 说明用了哪个文件作为输入
- 如需下一步校验，给出可检查项（sheet 数、行数、字段）
"""

    VALIDATOR_PROMPT = """你是 Validator 子 agent，只负责根据用户原始需求校验最近一次生成的结果是否满足要求。

## 核心职责
1. 对照用户需求检查生成的文件、工具结果或中间产物
2. 判断最近一次结果是否已经满足用户最终目标；如果它只是中间结果，必须明确判定为未完成
3. 若未完成，只需告诉主 agent 用户最终目标是什么，现阶段已经完成了哪些

## 敖江流域子任务编码
- 子任务：`霍口水库断面预报`、`霍口水库~山仔水库区间断面预报`、`山仔水库~水动力模型区间断面预报`、`水动力模型区间断面预报`、`桂湖溪流域出口断面预报`、`牛溪流域出口断面预报`
- stationCode：`33c76b8bd9384486a945c2fc7fd622eb`、`20001`、`30001`、`40001`、`GE2AG000000L`、`GE2AF000000R`
- 详细信息查看 aojiang-hydro SKILL.md文档

## 强约束
- 不负责重新生成文件
- 不负责替代执行
- 不得模糊表述“应该差不多正确”，必须明确列出检查项

## 判定规则
- 只有当最近一次结果已经直接满足用户最终交付目标时，才能判定 `overall_status=pass`
- 如果最近一次结果已经明确生成了与用户目标直接对应的最终文件（如 `.xlsx`、`.docx`、`.png`、`.pdf`）或最终图像/报告，并且用户要的就是这类交付物，应判定 `overall_status=pass`
- 如果最近一次结果只是 `input.json`、中间 `.json/.csv`、草稿 Excel、临时脚本、校验意见或“可以继续下一步”的提示，必须判定 `overall_status=fail`
- 当判定为 `fail` 时，只返回“未完成”和用户最终目标，不要输出操作建议

## 输出格式
- 只输出一个 JSON 对象，不要输出 Markdown，不要输出代码块，不要输出额外解释
- JSON 字段如下：
- `overall_status`: `pass` 或 `fail`
- `is_final_goal_met`: `true` 或 `false`
- `final_goal`: 用户最终目标的简短中文描述；若已完成可返回空字符串

## Excel/文件类默认检查项
- 如果结果是 Excel：检查 sheet 数、sheet 命名、每个 sheet 的行数/列名、时间轴一致性
- 如果结果是中间 JSON/CSV：检查记录数、关键字段、分组键（如断面/模型）是否完整
- 如果是检查json文件：如果json文件中有stationCode字段，需要重点校验stationCode与station.md文件中的stationCode是否一致！！
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
        self.base_tools: List[BaseTool] = [get_skill, run_script, exec_bash, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, read_artifact, knowledge_search, add_knowledge, web_search, add_memory, search_memory]

        skill_catalog = "\n".join(
            f"- {s.name}: {s.description}" for s in self.skills
        ) + "\n- get_skill: 按需获取任意技能的完整参数说明"

        self._skill_catalog = skill_catalog
        self._active_user_input = ""

        self.python_tools: List[BaseTool] = [get_skill, run_script, exec_bash, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, read_artifact]
        self.excel_tools: List[BaseTool] = [get_skill, run_script, exec_bash, exec_python_file, write_text_file, search_tool_error_memory, search_artifacts, read_artifact]
        self.validator_tools: List[BaseTool] = [get_skill, run_script, exec_python_file, search_memory]

        self.python_specialist_executor = self._build_specialized_executor(self.PYTHON_SPECIALIST_PROMPT, self.python_tools, max_iterations=50)
        self.excel_specialist_executor = self._build_specialized_executor(self.EXCEL_SPECIALIST_PROMPT, self.excel_tools, max_iterations=50)
        self.validator_executor = self._build_specialized_executor(self.VALIDATOR_PROMPT, self.validator_tools, max_iterations=50)

        self._executors: Dict[str, AgentExecutor] = {
            "python_specialist": self.python_specialist_executor,
            "excel_specialist": self.excel_specialist_executor,
            "validator": self.validator_executor,
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

    def _run_specialist_task(self, stage_name: str, task: str) -> str:
        task = (task or "").strip()
        if not task:
            return f"错误：委派给 {stage_name} 的 task 不能为空"

        executor = self._executors.get(stage_name)
        if executor is None:
            return f"错误：未找到子 agent `{stage_name}`"

        result = self._invoke_executor(executor, task)
        self._record_intermediate_steps(result)
        output = (result.get("output", "") or "").strip()
        output = self._normalize_specialist_output(result, output)
        if not output:
            return f"{stage_name} 未返回有效结果"

        stage_label = {
            "python_specialist": "Python Specialist",
            "excel_specialist": "Excel Specialist",
            "validator": "Validator",
        }.get(stage_name, stage_name)

        if stage_name == "validator":
            payload = {
                "stage": stage_name,
                "stage_label": stage_label,
                "result_type": "validation",
                "status": "completed",
                "user_goal": self._active_user_input,
                "summary": output,
                "validation": self._parse_validator_output(output),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        validation_output = self._run_validator_check(task, output)
        payload = {
            "stage": stage_name,
            "stage_label": stage_label,
            "result_type": "intermediate",
            "status": "completed",
            "user_goal": self._active_user_input,
            "task": task,
            "summary": output,
            "artifacts": self._extract_artifact_paths(output),
            "validation": self._parse_validator_output(validation_output),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_delegation_tools(self) -> List[BaseTool]:
        def delegate_python_specialist(task: str = "") -> str:
            return self._run_specialist_task("python_specialist", task)

        def delegate_excel_specialist(task: str = "") -> str:
            return self._run_specialist_task("excel_specialist", task)

        def delegate_validator(task: str = "") -> str:
            return self._run_specialist_task("validator", task)

        return [
            StructuredTool.from_function(
                func=delegate_python_specialist,
                name="delegate_python_specialist",
                description="当你已经完成任务拆分，且子任务属于数据提取、日志解析、文件结构化、中间 JSON/CSV、临时 Python 脚本、input.json 构造时调用。尤其适用于‘先解析文件/日志，再继续下游处理’的第一步。",
                args_schema=SpecialistTaskInput,
            ),
            StructuredTool.from_function(
                func=delegate_excel_specialist,
                name="delegate_excel_specialist",
                description="当你已经完成任务拆分，且子任务属于 Excel 结构设计、多工作表导出、Summary + 分对象工作表、按断面/按对象分 sheet 时调用。尤其适用于‘已有中间结构化数据，下一步生成 Excel’的场景。",
                args_schema=SpecialistTaskInput,
            ),
            StructuredTool.from_function(
                func=delegate_validator,
                name="delegate_validator",
                description="当你已经拿到中间结果或最终文件，且需要对照用户需求检查是否满足要求时调用。尤其适用于 Excel/JSON/模型结果生成后的最后一步复核。",
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
        # 就因为规则命中而提前切到子 agent，导致执行顺序和展示逻辑混乱。
        return ["orchestrator"]

    def _build_initial_loop_state(self, user_input: str) -> AgentLoopState:
        initial_step = PlanStep(
            step_id="step-1",
            title="理解目标并开始执行",
            executor="orchestrator",
            purpose="先由主 agent 理解最终目标、选择技能与子 agent，并产出第一轮结果。",
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

        if stage_name in {"python_specialist", "excel_specialist", "validator"}:
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
            validation = dict(payload.get("validation") or {})
            if self._artifacts_satisfy_user_goal(payload):
                validation.update({
                    "overall_status": "pass",
                    "is_final_goal_met": True,
                    "final_goal": "",
                })
                payload["result_type"] = "final"
            payload["validation"] = validation
            return payload

        return delegated_payload

    def _build_step_input(self, state: AgentLoopState, step: PlanStep) -> str:
        if step.input_text:
            return step.input_text

        latest_payload = state.latest_payload or {}

        if step.executor == "excel_specialist" and latest_payload:
            return (
                f"请根据原始用户需求和已有中间结果生成最终 Excel。\n\n"
                f"[原始用户需求]\n{state.original_input}\n\n"
                f"[最近一次中间结果]\n{latest_payload.get('summary', '')}\n\n"
                f"[最近一次产物]\n{json.dumps(latest_payload.get('artifacts', []), ensure_ascii=False, indent=2)}\n\n"
                "要求输出真正可交付的 Excel 文件，而不是继续返回中间 JSON/CSV。"
            )

        if step.executor == "validator" and state.previous_outputs:
            latest_summary = state.previous_outputs[-1][1]
            return (
                "请根据原始用户需求检查最近一次执行结果是否满足要求。\n\n"
                f"[原始用户需求]\n{state.original_input}\n\n"
                f"[最近一次子agent输出]\n{latest_summary}"
            )

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
        if step.executor == "validator":
            validation = (payload or {}).get("validation") or self._parse_validator_output(stage_output)
            return {
                "scope": "goal",
                "status": "pass" if validation.get("is_final_goal_met") else "fail",
                "reason": str(validation.get("final_goal", "") or ""),
                "goal_satisfied": bool(validation.get("is_final_goal_met")),
                "requires_replan": not bool(validation.get("is_final_goal_met")),
                "validation": validation,
                **({
                    **base_progress,
                    "failed_step": {
                        "step_id": step.step_id,
                        "title": step.title,
                        "executor": step.executor,
                        "purpose": step.purpose,
                        "status": step.status,
                    },
                } if not validation.get("is_final_goal_met") else base_progress),
            }

        if payload:
            validation = payload.get("validation") or {}
            should_continue = self._should_force_continue(payload)
            return {
                "scope": "step",
                "status": "fail" if should_continue else "pass",
                "reason": str(validation.get("final_goal", "") or ""),
                "goal_satisfied": not should_continue,
                "requires_replan": should_continue,
                "validation": validation,
                **({
                    **base_progress,
                    "failed_step": {
                        "step_id": step.step_id,
                        "title": step.title,
                        "executor": step.executor,
                        "purpose": step.purpose,
                        "status": step.status,
                    },
                } if should_continue else base_progress),
            }

        text = (stage_output or "").strip()
        return {
            "scope": "step",
            "status": "pass" if text else "fail",
            "reason": "" if text else "当前步骤未产出有效结果",
            "goal_satisfied": False,
            "requires_replan": not bool(text),
            "validation": {},
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
            item = {
                "step_id": step.step_id,
                "title": step.title,
                "executor": step.executor,
                "purpose": step.purpose,
                "status": step.status,
            }
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

    def _verify_goal_state(self, state: AgentLoopState) -> Dict[str, Any]:
        payload = state.latest_payload or {}
        validation = payload.get("validation") or state.final_verification or {}
        needs = self._needs_final_deliverable(state.original_input)
        artifacts = state.artifacts or self._collect_artifacts_from_payload(payload)
        artifacts_ok = self._artifacts_cover_needs(artifacts, needs)
        validation_ok = bool(validation.get("is_final_goal_met")) or not any(needs.values())
        goal_satisfied = artifacts_ok and validation_ok
        progress = self._classify_step_completion(state)
        reason = str(validation.get("final_goal", "") or "")
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
            lines.append(line)
        return "\n".join(lines)

    def _build_progress_context(self, state: AgentLoopState, verification: Dict[str, Any]) -> str:
        completed_steps = verification.get("completed_steps") or []
        pending_steps = verification.get("pending_steps") or []
        failed_step = verification.get("failed_step") or {}
        reusable_artifacts = verification.get("reusable_artifacts") or state.artifacts or []
        latest_payload = state.latest_payload or {}

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

    def _mark_artifacts_validated(self, state: AgentLoopState, validation: Dict[str, Any]) -> None:
        if not validation.get("is_final_goal_met"):
            return
        for record in state.artifact_registry:
            if record.get("deliverable_candidate"):
                record["validated"] = True
                record["updated_at"] = datetime.now().isoformat()

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
                    "final_verification": state.final_verification,
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
        next_executor = failed_executor or "orchestrator"
        next_title = "根据校验反馈继续执行"

        if verification.get("scope") == "step" and failed_executor:
            next_title = f"继续完成：{failed_step.get('title', '当前失败步骤')}"
        elif verification.get("scope") == "goal":
            if needs["excel"] and not self._artifacts_cover_needs(artifacts or state.artifacts, {"excel": True, "image": False, "report": False}):
                next_executor = "excel_specialist"
                next_title = "基于已有结果补齐最终 Excel"
            elif failed_executor:
                next_executor = failed_executor
                next_title = f"继续完成：{failed_step.get('title', '当前失败步骤')}"

        if needs["excel"] and not self._artifacts_cover_needs(artifacts, {"excel": True, "image": False, "report": False}):
            if latest_stage == "python_specialist" or artifacts:
                next_executor = "excel_specialist"
                next_title = "基于中间结果生成最终 Excel"
        elif latest_stage == "excel_specialist":
            next_executor = "validator"
            next_title = "复核最终交付是否满足目标"

        step_id = f"step-{len(state.plan_steps) + 1}"
        progress_context = self._build_progress_context(state, verification)
        reason = str(verification.get("reason", "") or "根据校验反馈继续推进任务")
        step = PlanStep(
            step_id=step_id,
            title=next_title,
            executor=next_executor,
            purpose=reason,
            input_text=(
                f"[原始用户需求]\n{state.original_input}\n\n"
                f"[当前待完成目标]\n{reason}\n\n"
                f"{progress_context}\n\n"
                "请只继续完成当前未完成步骤，禁止默认重跑已经完成的上游步骤。"
            ),
        )
        state.plan_steps.append(step)
        state.replan_count += 1

    def _run_agent_loop(self, user_input: str, callbacks: Optional[List[Any]] = None, event_sink: Optional[Any] = None) -> AgentLoopState:
        state = self._build_initial_loop_state(user_input)
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
                    state.terminal_status = "completed"
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
            result = self._invoke_executor(self._executors[step.executor], stage_input, callbacks=callbacks)
            state.tool_calls.extend(self._record_intermediate_steps(result))
            if event_sink:
                for tool_event in self._build_tool_activity_events(result):
                    event_sink(tool_event)
            stage_output = (result.get("output", "") or "").strip()
            if step.executor != "orchestrator":
                stage_output = self._normalize_specialist_output(result, stage_output)

            payload = self._extract_result_payload(step.executor, result, stage_output)
            verification = self._verify_step_result(step, payload, stage_output)

            step.status = "completed" if verification.get("status") == "pass" else "failed"
            step.output_text = stage_output
            step.verification = verification
            state.previous_outputs.append((step.executor, stage_output))
            state.final_output = stage_output or state.final_output
            state.latest_payload = payload
            state.final_verification = verification.get("validation") or state.final_verification
            new_artifacts = self._register_artifacts(state, step, payload)
            self._mark_artifacts_validated(state, verification.get("validation") or {})
            self._record_journal_entry(state, step=step, verification=verification, stage_input=stage_input)
            self._persist_loop_state(state)

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
                state.terminal_status = "completed"
                state.final_verification = goal_verification
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
                    })

        if state.terminal_status == "running":
            state.terminal_status = "budget_exceeded"
            if state.latest_payload:
                state.final_output = self._build_forced_continuation_input(user_input, state.latest_payload)
            self._persist_loop_state(state)
        return state

    def _build_stage_user_input(self, stage_name: str, original_input: str, previous_outputs: List[Tuple[str, str]]) -> str:
        if stage_name == "validator":
            latest_summary = previous_outputs[-1][1] if previous_outputs else ""
            return (
                f"请根据原始用户需求检查最近一次执行结果是否满足要求。\n\n"
                f"[原始用户需求]\n{original_input}\n\n"
                f"[最近一次子agent输出]\n{latest_summary}"
            )

        if stage_name == "excel_specialist" and previous_outputs:
            latest_summary = previous_outputs[-1][1]
            return (
                f"请根据原始用户需求和已有中间结果生成最终 Excel。\n\n"
                f"[原始用户需求]\n{original_input}\n\n"
                f"[上游中间结果]\n{latest_summary}\n\n"
                f"要求你优先基于上游生成的中间文件或结构化结果设计 Excel，而不是重新解析原始日志或重新构造大数组。"
            )

        if not previous_outputs:
            return original_input

        context_blocks = [f"[{name}]\n{output}" for name, output in previous_outputs if output]
        return (
            f"[原始用户需求]\n{original_input}\n\n"
            f"[已有中间结果]\n" + "\n\n".join(context_blocks)
        )

    def _invoke_executor(self, executor: AgentExecutor, user_input: str, callbacks: Optional[List[Any]] = None) -> Dict[str, Any]:
        agent_input = {
            "input": user_input,
            "current_time_context": self._get_current_time_context(),
            "current_system_context": self._get_current_system_context(),
            "chat_history": self._get_chat_history_messages(),
        }

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

        if len(previous_outputs) >= 2 and previous_outputs[-1][0] == "validator":
            final_output = f"{previous_outputs[-2][1]}\n\n[校验结果]\n{previous_outputs[-1][1]}"

        return final_output, previous_outputs, tool_calls

    def _run_validator_check(self, delegated_task: str, specialist_output: str) -> str:
        artifacts = self._extract_artifact_paths(specialist_output)
        validator_input = (
            "请根据用户最终目标验证最近一次结果是否已经满足最终交付要求。"
            "如果它只是中间结果，必须判定 fail。\n\n"
            f"[原始用户需求]\n{self._active_user_input or delegated_task}\n\n"
            f"[当前委派子任务]\n{delegated_task}\n\n"
            f"[最近一次结果]\n{specialist_output}\n\n"
            f"[最近一次结果中提取到的产物路径]\n{json.dumps(artifacts, ensure_ascii=False, indent=2)}"
        )
        result = self._invoke_executor(self.validator_executor, validator_input)
        self._record_intermediate_steps(result)
        output = (result.get("output", "") or "").strip()
        return self._normalize_specialist_output(result, output)

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
    def _parse_validator_output(text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            return {
                "overall_status": "fail",
                "is_final_goal_met": False,
                "final_goal": "未能确认用户最终目标是否已经完成",
                "raw_output": raw,
            }

        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                parsed.setdefault("overall_status", "fail")
                parsed.setdefault("is_final_goal_met", parsed.get("overall_status") == "pass")
                parsed.setdefault("final_goal", "" if parsed.get("overall_status") == "pass" else "用户最终目标尚未完成")
                return parsed
        except Exception:
            pass

        lower = cleaned.lower()
        is_pass = '"overall_status"' not in lower and ("overall_status: pass" in lower or "pass" == lower)
        return {
            "overall_status": "pass" if is_pass else "fail",
            "is_final_goal_met": is_pass,
            "final_goal": "" if is_pass else "用户最终目标尚未完成",
            "raw_output": raw,
        }

    @staticmethod
    def _extract_latest_delegation_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for step in reversed(result.get("intermediate_steps", [])):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue
            action, observation = step[0], step[1]
            tool_name = getattr(action, "tool", "")
            if tool_name not in {"delegate_python_specialist", "delegate_excel_specialist", "delegate_validator"}:
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

        validation = payload.get("validation") or {}
        overall_status = str(validation.get("overall_status", "")).strip().lower()
        is_final_goal_met = validation.get("is_final_goal_met")

        if FloodAgent._artifacts_satisfy_user_goal(payload):
            return False

        if overall_status == "fail":
            return True
        if is_final_goal_met is False:
            return True
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
        validation = payload.get("validation") or {}
        return (
            f"[原始用户需求]\n{original_user_input}\n\n"
            "[系统强制校验结论]\n"
            "最近一次子任务结果只是中间结果，尚未满足用户最终目标。你现在不得结束，必须继续执行后续步骤。\n\n"
            f"[最近一次中间结果]\n{payload.get('summary', '')}\n\n"
            f"[校验结果]\n{json.dumps(validation, ensure_ascii=False, indent=2)}\n\n"
            "请根据校验结论继续调用合适的技能、工具或子 agent，直到真正完成用户最终目标。"
        )

    @staticmethod
    def _build_validation_feedback_message(payload: Optional[Dict[str, Any]], should_continue: bool) -> str:
        if not payload:
            return ""

        validation = payload.get("validation") or {}
        overall_status = str(validation.get("overall_status", "")).strip().lower() or "unknown"
        is_final_goal_met = validation.get("is_final_goal_met")
        final_goal = str(validation.get("final_goal", "") or "").strip()

        lines = [
            "[调度校验]",
            f"- overall_status: {overall_status}",
            f"- is_final_goal_met: {is_final_goal_met}",
        ]
        if final_goal:
            lines.append(f"- final_goal: {final_goal}")

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
                logger.warning("子 agent 在停止前已产出有效结果，改用最近一次成功工具输出作为返回内容")
                return fallback
            return "子 agent 达到迭代次数或执行时间上限，且未能整理出最终子任务结论。"

        if "Agent stopped due to iteration limit or time limit." in text:
            fallback = self._extract_specialist_fallback_output(result)
            if fallback:
                logger.warning("子 agent 输出中包含停止提示，已替换为最近一次成功工具输出")
                return fallback
            return text.replace(
                "Agent stopped due to iteration limit or time limit.",
                "子 agent 达到迭代次数或执行时间上限。",
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
                    return []
                if event_type == "artifact_created":
                    artifact = event.get("artifact", {}) or {}
                    path = str(artifact.get("path", "") or "")
                    filename = os.path.basename(path) if path else ""
                    if not filename:
                        return []
                    if artifact.get("artifact_type") == "image":
                        return [{"type": "image_generated", "filename": filename, "filepath": path}]
                    return [{"type": "file_generated", "filename": filename, "filepath": path}]
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
