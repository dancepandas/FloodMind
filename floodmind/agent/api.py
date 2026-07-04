"""
FloodMind Agent — 轻量级 SDK 入口

面向嵌入场景的便捷类，封装 NativeFloodAgent(bare=True)。
开发者只需传入 ModelClient + 自定义工具 + 提示词即可使用。

用法:
    from floodmind import Agent, ModelClient, build_agent_tool

    llm = ModelClient(api_key="sk-xxx", base_url="https://...", model_name="my-model")

    def query_data(station: str) -> str:
        return f"{station} 数据..."

    # 事件回调（可选）：每个流事件都会推送，无需手动迭代 stream
    def on_event(event):
        if event["type"] == "token_usage":
            print("token 用量:", event)

    agent = Agent(
        llm=llm,
        tools=[build_agent_tool(func=query_data, name="QueryData", description="查询数据")],
        system_prompt="你是数据分析助手。",
        on_event=on_event,
        max_iterations=20,
    )

    result = agent.run("查一下 XX 站的数据")       # 非流式
    for event in agent.stream("查一下 XX 站"):      # 流式
        print(event)

    print(agent.last_usage)   # 本次调用的 token 用量
    print(agent.artifacts)    # 本次调用收集到的产物事件
"""

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.model_client import ModelClient

logger = logging.getLogger(__name__)


class Agent:
    """FloodMind 轻量级 Agent — 嵌入式 SDK 入口。

    将 FloodMind Agent 嵌入到任何 Python 系统中：
    - 传入 LLM 客户端和自定义工具
    - 通过 run() 或 stream() 获取结果
    - 流式事件可直接推送给自建前端，或通过 on_event 回调订阅

    Args:
        llm: ModelClient 实例（必填）
        tools: build_agent_tool() 构建的工具列表
        system_prompt: 自定义系统提示词
        memory: DualMemory 实例（不传则自动创建内存记忆）
        session_id: 会话 ID
        enable_search: 启用 WebSearch 工具
        enable_reasoning: 启用推理模式
        on_event: 流式事件回调 ``Callable[[dict], None]``。run()/stream() 期间每个事件
            都会调用一次。回调内抛出的异常会被捕获并记录，不会中断执行流。
        permission_handler: 工具调用审批钩子 ``Callable[[tool_name, tool_input], bool]``。
            每次工具执行前同步调用，返回 False 则拒绝该次调用（工具不执行，模型收到拒绝
            信息）。bare 模式默认放行所有调用，此钩子提供可选的安全网关。
        max_iterations: Agent 循环最大迭代轮数（默认 50）。

    Attributes:
        last_usage: 最近一次 run()/stream() 的 token 用量累加
            (``{"prompt_tokens","completion_tokens","total_tokens"}``)，每次调用刷新。
        artifacts: 最近一次 run()/stream() 收集到的产物事件列表
            (``file_generated``/``image_generated``)，每次调用刷新。
        raw: 底层 NativeFloodAgent 实例（高级用法）。
    """

    def __init__(
        self,
        llm: ModelClient,
        tools: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
        memory: Optional[Any] = None,
        session_id: str = "",
        enable_search: bool = False,
        enable_reasoning: bool = False,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        permission_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        max_iterations: int = 50,
    ):
        if memory is None:
            from floodmind.memory.dual_memory import DualMemory
            sid = session_id or "sdk-agent"
            memory = DualMemory(session_id=sid, context_window=32768)

        self._on_event = on_event
        self._last_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._artifacts: List[Dict[str, Any]] = []

        self._agent = NativeFloodAgent(
            llm_service=llm,
            memory=memory,
            session_id=session_id or "sdk-agent",
            enable_search=enable_search,
            enable_reasoning=enable_reasoning,
            bare=True,
            tools=tools or [],
            system_prompt=system_prompt,
            permission_handler=permission_handler,
            max_iterations=max_iterations,
        )

    # ── 事件迭代与收集 ──────────────────────────────────────────────
    def _collect_event(self, event: Dict[str, Any]) -> None:
        """从事件中收集 token 用量与产物（维护 last_usage / artifacts）。"""
        etype = event.get("type")
        if etype == "token_usage":
            self._last_usage["prompt_tokens"] += int(event.get("prompt_tokens") or 0)
            self._last_usage["completion_tokens"] += int(event.get("completion_tokens") or 0)
            self._last_usage["total_tokens"] += int(event.get("total_tokens") or 0)
        elif etype in ("file_generated", "image_generated"):
            self._artifacts.append(event)

    def _iter(self, message: str) -> Iterator[Dict[str, Any]]:
        """统一事件迭代器：重置本次结果 → 收集 → 触发 on_event → yield。

        run() 与 stream() 都走这里，保证两者都触发 on_event 并维护 last_usage/artifacts。
        """
        self._last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._artifacts = []
        for event in self._agent.stream(message):
            self._collect_event(event)
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception as e:
                    logger.warning("on_event 回调异常（已忽略，不中断流）: %s", e)
            yield event

    # ── 对外执行接口 ────────────────────────────────────────────────
    def run(self, message: str) -> str:
        """非流式执行，返回最终回答文本。

        内部迭代 stream 收集 ``final_text``（优先），缺失时回退累加 ``answer_delta``。
        期间同样触发 ``on_event`` 并维护 ``last_usage``/``artifacts``。
        """
        full_answer = ""
        for event in self._iter(message):
            etype = event.get("type")
            if etype == "final_text":
                full_answer = event.get("content", "")
            elif etype == "answer_delta" and not full_answer:
                full_answer += event.get("content", "")
        return full_answer or "抱歉，处理您的请求时未能生成回答。"

    def stream(self, message: str) -> Iterator[Dict[str, Any]]:
        """流式执行，产出结构化事件 dict。

        事件按类别（bare 模式下部分事件如 permission_ask 默认不触发）：

        思考 / 回答:
          - answer_delta:  回答文本增量      {"type": "answer_delta", "content": "..."}
          - thought_delta: 思考过程增量      {"type": "thought_delta", "content": "..."}
          - final_text:    最终完整回答      {"type": "final_text", "content": "..."}

        工具:
          - action_start:  工具调用开始      {"type": "action_start", "tool_name": "...", "status": "running", "call_id"?, "step_key"?}
          - action_end:    工具调用结束      {"type": "action_end", "tool_name": "...", "content": "...", "call_id"?, "step_key"?}

        计划:
          - workflow_plan: 执行计划          {"type": "workflow_plan", "title": "...", "steps": [...]}
          - workflow_step: 步骤进度          {"type": "workflow_step", "step_key": "...", "status": "running|completed|...", "subtasks"?}

        LLM 生命周期:
          - llm_step_start: LLM 调用开始     {"type": "llm_step_start", "iteration": N, "model"?}
          - llm_step_end:   LLM 调用结束     {"type": "llm_step_end", "finish_reason": "...", "tokens": {...}}
          - retry_attempt:  模型重试         {"type": "retry_attempt", "attempt": N}
          - context_compress_start/done: 上下文压缩

        产物:
          - file_generated:  文件产物        {"type": "file_generated", "filename": "...", "download_url"?, "filepath"?, "size"?}
          - image_generated: 图片产物        {"type": "image_generated", "filename": "...", "image_url"?, "download_url"?, "size"?}

        系统:
          - token_usage:    token 用量       {"type": "token_usage", "prompt_tokens", "completion_tokens", "total_tokens"}
          - heartbeat:      心跳             {"type": "heartbeat"}
          - error:          错误             {"type": "error", "content": "..."}
          - llm_token_error: 账号余额不足    {"type": "llm_token_error", "content": "..."}
          - permission_ask/resolved: 权限询问与裁决（bare 模式默认不触发）

        产物也可在执行后通过 ``agent.artifacts`` 获取；token 用量通过 ``agent.last_usage`` 获取。
        """
        yield from self._iter(message)

    def chat(self, message: str) -> str:
        """run() 的别名。"""
        return self.run(message)

    # ── 只读结果访问 ────────────────────────────────────────────────
    @property
    def last_usage(self) -> Dict[str, int]:
        """最近一次 run()/stream() 的 token 用量（本次调用累加，调用结束刷新）。"""
        return dict(self._last_usage)

    @property
    def artifacts(self) -> List[Dict[str, Any]]:
        """最近一次 run()/stream() 收集到的产物事件（file_generated/image_generated）。"""
        return list(self._artifacts)

    @property
    def raw(self) -> NativeFloodAgent:
        """访问底层 NativeFloodAgent 实例（高级用法）。"""
        return self._agent

    def __repr__(self) -> str:
        return f"<Agent tools={len(self._agent._orchestrator_registry.all())} session={self._agent.session_id}>"
