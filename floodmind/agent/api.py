"""
FloodMind Agent — 轻量级 SDK 入口

面向嵌入场景的便捷类，封装 NativeFloodAgent(bare=True)。
开发者只需传入 ModelClient + 自定义工具 + 提示词即可使用。

用法:
    from floodmind import Agent, ModelClient, build_agent_tool
    # 或: from floodmind.agent import Agent

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

import inspect
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

from floodmind.agent.native.native_flood_agent import NativeFloodAgent
from floodmind.agent.native.model_client import ModelClient
from floodmind.memory.session_manager import validate_session_id

logger = logging.getLogger(__name__)


class Agent:
    """FloodMind 轻量级 Agent — 嵌入式 SDK 入口。

    TARGET desktop contract:
    - ``conversation_id`` / ``task_id`` / ``run_id`` / ``thread_id`` expose the
      canonical identity of the latest run.
    - ``stream()`` yields best-effort preview events; preview parts carry
      ``attempt_id`` / ``part_id`` when available.
    - ``events_after(sequence)`` returns Journal-derived committed public events
      with canonical ``sequence`` values for replay and preview reconciliation.
    - ``resume(checkpoint_id)`` resumes through Journal replay and reconciliation.
    - ``run()`` and ``chat()`` return the committed final answer.

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
        permission_handler: 工具调用审批钩子 ``Callable[[tool_name, tool_input], Optional[bool]]``。
            每次工具执行前同步调用，是宿主的**预授权**钩子：
            - 返回 ``True``：宿主同意执行 → 策略级 ASK 自动放行（跳过用户交互确认，桌面
              always-trust 模式）；但不可翻越 SDK 安全硬门——子代理 tier、planning 模式、
              路径校验、危险命令、全局 deny 规则照常生效（钩子只能收紧不能放开）。
            - 返回 ``False``：宿主拒绝 → DENY（工具不执行，模型收到拒绝信息）；
            - 返回 ``None``（或未处理异常）：宿主无意见 → 交给 SDK 正常判断（permission_service 规则照常）。
        permission_decision_hook: host-level 权限决策钩子
            ``Callable[[tool_name, tool_input, sdk_decision, permission_policy], PermissionDecision]``。
            在 SDK 完成基础权限判断后调用，宿主可基于 SDK 原始决策调整最终行为：保留 DENY/ASK、
            把 ALLOW 升级为 ASK（走 permission_ask 交互确认）或 DENY。钩子只能收紧不能放开——
            SDK 的安全拒绝（路径越界/危险命令/子代理分层/planning 硬门）不可被覆盖；钩子异常或
            返回非法值时保留 SDK 原决策。desktop 可用它替代对内部 registry 的 monkey patch。
        max_iterations: Agent 循环最大迭代轮数（默认 999，有 auto-compact + DOOM LOOP 兜底）。
        workspace: 工作区对象（``floodmind.agent.runtime.contracts.workspace.Workspace``）。
            嵌入式宿主（如桌面端）可显式注入，避免跨线程 contextvar 丢失。未传时 SDK
            默认使用当前进程 cwd 创建 folder-first workspace；底层 contextvar 仅作为 legacy
            adapter 回退。运行期可用 ``bind_workspace`` 切换。
        tool_loading: 工具加载策略。``None`` 使用 settings 默认；``False`` 为 eager 旧行为；
            ``True`` 为 progressive 默认；也可传 ``floodmind.ToolLoadingConfig``。
        skill_roots: 宿主额外提供的只读 Skill 发现根；每个 Agent 使用独立 Registry。
        skill_writable_root: Skill CRUD 与自动生成的唯一写入根，同时只加入 Skill 读取授权，
            不会自动加入 Workspace 的普通写入根。
        input_guardrails: 输入 Guardrail 序列。每次 LLM 调用前接收完整 messages；
            返回 ``GuardrailResult(tripwire_triggered=True)`` 时 fail-closed 终止。
        output_guardrails: 输出 Guardrail 序列。最终答案在放流前校验；首次 tripwire
            自动注入修正提示重试一次，第二次触发终止。
        handoffs: ``HandoffTarget`` 序列。模型选择 handoff 工具后，目标 Agent 的
            ModelClient、提示词与工具运行时接管同一 run。
        trace_processors: 实现 ``on_event(EventEnvelope)`` 的处理器序列。旁路消费
            committed canonical 事件，异常不会影响 journal 写入。
        bare: 是否为 bare 嵌入模式（默认 True）。``True`` 仅注册自定义工具；``False`` 走
            NativeFloodAgent 完整 runtime（内置工具、MCP、Skill、权限 ASK 事件、workspace 绑定）。
            完整 runtime 下 ``tools=None`` 保留原生默认工具集。

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
        permission_decision_hook: Optional[Callable] = None,
        max_iterations: int = 999,
        workspace: Optional[Any] = None,
        tool_loading: Optional[Any] = None,
        bare: bool = True,
        skill_roots: Optional[Sequence[Union[str, Path]]] = None,
        skill_writable_root: Optional[Union[str, Path]] = None,
        mcp_pool: Optional[Any] = None,
        input_guardrails: Optional[Sequence[Callable]] = None,
        output_guardrails: Optional[Sequence[Callable]] = None,
        trace_processors: Optional[Sequence[Callable]] = None,
        handoffs: Optional[Sequence[Any]] = None,
    ):
        sid = validate_session_id(session_id or f"sdk-{uuid.uuid4().hex}")
        if memory is None:
            from floodmind.memory.dual_memory import DualMemory
            memory = DualMemory(session_id=sid, context_window=self._resolve_context_window(llm))

        if workspace is None:
            from floodmind.agent.runtime.contracts.workspace import Workspace
            workspace = Workspace.from_cwd(session_id=sid).ensure()

        self._on_event = on_event
        if on_event is not None and inspect.iscoroutinefunction(on_event):
            raise TypeError(
                "on_event 不支持 async 函数：FloodMind 运行时是同步的，请提供同步回调"
                "（或在线程内用 asyncio.run 桥接）。"
            )
        self._journal_authority: Optional[Any] = None
        self._last_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._artifacts: List[Dict[str, Any]] = []

        self._agent = NativeFloodAgent(
            llm_service=llm,
            memory=memory,
            session_id=sid,
            enable_search=enable_search,
            enable_reasoning=enable_reasoning,
            bare=bare,
            tools=tools,
            system_prompt=system_prompt,
            permission_handler=permission_handler,
            permission_decision_hook=permission_decision_hook,
            max_iterations=max_iterations,
            workspace=workspace,
            tool_loading=tool_loading,
            skill_roots=skill_roots,
            skill_writable_root=skill_writable_root,
            mcp_pool=mcp_pool,
            input_guardrails=list(input_guardrails or []),
            output_guardrails=list(output_guardrails or []),
            trace_processors=list(trace_processors or []),
            handoffs=list(handoffs or []),
        )

    @staticmethod
    def _resolve_context_window(llm: Any) -> int:
        """记忆窗口取当前激活模型的 context_window；无全局配置时回退默认值。

        显式传入 llm 的宿主不应被 settings.json 的 providers 配置缺失阻塞构造——
        全局配置只用于提升默认记忆窗口精度，缺失时用 SDK 默认值（32768）降级。
        """
        from floodmind.memory.dual_memory import DEFAULT_CONTEXT_WINDOW_FALLBACK

        try:
            from floodmind.config.model_resolver import resolve_model
            return resolve_model().context_window
        except Exception as exc:
            logger.warning(
                "resolve_model 失败（%s），记忆窗口回退为 %d。"
                "如需精确记忆窗口，请在 ~/.floodmind/settings.json 配置 providers，"
                "或显式传入 memory=DualMemory(session_id=..., context_window=...)。",
                exc,
                DEFAULT_CONTEXT_WINDOW_FALLBACK,
            )
            return DEFAULT_CONTEXT_WINDOW_FALLBACK

    def bind_workspace(self, ws: Any) -> None:
        """绑定/切换工作区（透传给底层 NativeFloodAgent）。

        嵌入式宿主（桌面端）替代跨线程不可靠的 contextvar 注入：任意线程调用，
        下一次 stream() 起在 SDK 子线程内重新生效。
        """
        self._agent.bind_workspace(ws)

    # ── 底层只读/操作代理（避免宿主直接访问 raw 内部） ──────────────
    @property
    def memory(self) -> Any:
        """底层 NativeFloodAgent 使用的 memory 对象。"""
        return self._agent.memory

    @property
    def session_id(self) -> str:
        """底层 NativeFloodAgent 的 session_id。"""
        return self._agent.session_id

    @property
    def conversation_id(self) -> str:
        """最近一次 run 的 canonical conversation identity。"""
        authority = self._journal_authority
        return getattr(self._agent, "conversation_id", "") or getattr(
            authority, "conversation_id", ""
        )

    @property
    def task_id(self) -> str:
        """最近一次 run 的 canonical task identity。"""
        return getattr(self._journal_authority, "task_id", "")

    @property
    def run_id(self) -> str:
        """最近一次 run 的 canonical run identity。"""
        return getattr(self._journal_authority, "run_id", "")

    @property
    def thread_id(self) -> str:
        """最近一次 run 的 canonical thread identity。"""
        return getattr(self._journal_authority, "thread_id", "")

    def events_after(self, sequence: int = 0) -> List[Dict[str, Any]]:
        """返回该 run 从 sequence 之后的公共 committed 事件。"""
        from floodmind.agent.sdk_events import project_canonical_many

        authority = self._journal_authority
        if authority is None:
            return []
        return project_canonical_many(authority.read_after(sequence))

    def resume(
        self,
        checkpoint_id: str,
        user_message: str = "",
    ) -> str:
        """Journal Replay + Reconciliation resume（TARGET desktop contract）。

        Mirrors ``cli.py:228-262``: ``ResumeService`` 走 fencing → 校验 →
        replay → reconcile → ``resume.started``+``resume.completed``，
        然后把已恢复的 JournalAuthority 直接绑到底层 executor
        （**不** 走 ``NativeFloodAgent.stream(resume_checkpoint_id=...)`` —
        那个路径在 memory-first runtime 下被显式拒绝），再用 ``run_from_state``
        在绑好的 authority 上驱动续接。

        Returns:
            续接产出的最终回答文本。
        """
        from floodmind.agent.native.executor import project_run_state_to_loop_state
        from floodmind.agent.native.types import AgentLoopState, RunContext
        from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
        from floodmind.agent.runtime.services.resume_service import ResumeService
        from floodmind.agent.runtime.services.artifact_service import ArtifactService
        from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext

        if not checkpoint_id:
            raise ValueError("resume requires checkpoint_id")

        workspace = getattr(self._agent, "_workspace", None) or getattr(self, "_workspace", None)
        base_dir = str(workspace.session_root) if workspace is not None else None
        if not base_dir:
            raise ValueError("Agent.resume requires a workspace with session_root")

        session_id = self.session_id or ""
        svc = CheckpointService(base_dir=base_dir)
        manifest = svc.load_manifest(session_id, checkpoint_id)
        identity = manifest.metadata or {}
        required = (
            "conversation_id", "task_id", "run_id", "thread_id", "turn_id", "runtime_dir",
        )
        missing = [key for key in required if not identity.get(key)]
        if missing:
            raise ValueError(
                f"checkpoint {checkpoint_id} missing journal identity: {', '.join(missing)}"
            )

        outcome = ResumeService().resume(
            runtime_dir=Path(identity["runtime_dir"]),
            conversation_id=identity["conversation_id"],
            task_id=identity["task_id"],
            run_id=identity["run_id"],
            thread_id=identity["thread_id"],
            turn_id=identity["turn_id"],
            checkpoint_id=checkpoint_id,
            user_message=user_message,
            session_id=session_id,
            checkpoint_service=svc,
        )
        authority = outcome.authority
        if authority is None:
            raise RuntimeError("ResumeService returned no JournalAuthority")

        runtime_dir = Path(identity["runtime_dir"])
        workspace_id = str(workspace.workspace_dir) if workspace is not None else ""
        artifact_service = ArtifactService(
            runtime_dir / "artifacts",
            authority=authority,
            allowed_roots=[workspace_id] if workspace_id else [],
        )
        context = RuntimeContext(
            conversation_id=identity["conversation_id"],
            task_id=identity["task_id"],
            run_id=identity["run_id"],
            thread_id=identity["thread_id"],
            turn_id=identity["turn_id"],
            actor_type="host",
            actor_id=session_id,
            agent_tier="main",
            runtime_mode="execution",
            workspace_id=workspace_id,
            permission_service=getattr(self._agent, "_permission_service", None),
            path_service=getattr(self._agent, "_path_service", None),
            background_service=getattr(self._agent, "_background_task_service", None),
            artifact_service=artifact_service,
            journal_authority=authority,
        )

        # Project RunState → AgentLoopState 并把已恢复 authority 绑到 executor。
        run_state = outcome.run_state
        loop_state = AgentLoopState(
            session_id=session_id,
            run_id=identity["run_id"],
            user_message=user_message,
            original_input=user_message or identity.get("user_message", ""),
        )
        loop_state = project_run_state_to_loop_state(loop_state, run_state)
        if loop_state.status in {"completed", "failed"}:
            loop_state.status = "awaiting_llm"
            loop_state.final_output = ""
        if user_message:
            loop_state.user_message = user_message
            loop_state.original_input = loop_state.original_input or user_message

        executor = self._agent._orchestrator_executor
        executor._journal_authority = authority
        self._agent._journal_authority = authority
        self._agent._last_loop_state = loop_state
        self._journal_authority = authority

        # §10.1/§4.4：公开 resume 进度事件（desktop 契约），与 Journal 的 resume.started 对齐。
        # 走续接所用 executor 的 event_bus，与续接产出的事件同一条公开流。
        _resume_bus = getattr(executor, "event_bus", None)
        if _resume_bus is not None:
            _resume_bus.emit({
                "type": "resume",
                "checkpoint_id": checkpoint_id,
                "status": "started",
            })

        run_ctx = RunContext(
            session_id=session_id,
            user_text=user_message,
            cwd=str(workspace.default_cwd) if workspace is not None else "",
            workspace_dir=str(workspace.workspace_dir) if workspace is not None else "",
            state_dir=str(workspace.state_dir) if workspace is not None else "",
            artifact_dir=str(workspace.artifact_dir) if workspace is not None else "",
            tmp_dir=str(workspace.tmp_dir) if workspace is not None else "",
            scripts_dir=str(workspace.scripts_dir) if workspace is not None else "",
            runtime_context=context,
        )
        self._agent._current_run_context = run_ctx

        try:
            result = executor.run_from_state(run_ctx, loop_state, run_state=run_state)
        finally:
            # fencing lease 覆盖整个 resumed run；终态后释放。
            outcome.lease.release()
            if _resume_bus is not None:
                _resume_bus.emit({
                    "type": "resume",
                    "checkpoint_id": checkpoint_id,
                    "status": "completed",
                })
        final_output = getattr(result, "final_output", "") or ""
        return final_output

    def clear_memory(self) -> None:
        """清空底层 NativeFloodAgent 的会话记忆。"""
        if hasattr(self._agent, "clear_memory"):
            self._agent.clear_memory()

    # ── 后台任务公开 API（desktop/LS 契约；宿主不再需要 agent.raw._background_task_service） ──
    def list_background_tasks(self) -> List[Dict[str, Any]]:
        """列出本会话后台任务（运行中 / 已完成 / 重启对账后的 orphaned / unknown）。

        每个条目: ``task_id / session_id / command / pid / status / exit_code /
        stdout_path / stderr_path / started_at / finished_at / tail / error``。
        """
        svc = getattr(self._agent, "_background_task_service", None)
        if svc is None:
            return []
        return [t.to_public_dict() for t in svc.list(self.session_id or "")]

    def kill_background_task(self, task_id: str) -> bool:
        """终止本会话指定后台任务（进程树 + 验证退出；kill 验证链）。"""
        svc = getattr(self._agent, "_background_task_service", None)
        if svc is None:
            return False
        return svc.kill(self.session_id or "", task_id)

    def cleanup(self) -> None:
        """释放资源：kill 本会话存活的后台任务（meta.json 保留供审计）。幂等。"""
        if hasattr(self._agent, "cleanup"):
            self._agent.cleanup()

    def __del__(self) -> None:
        """析构时兜底清理存活后台任务（不抛异常）。"""
        try:
            if hasattr(self, "_agent") and self._agent is not None:
                if hasattr(self._agent, "cleanup"):
                    self._agent.cleanup()
        except Exception:
            pass

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

    def _iter(self, message: str, **stream_kwargs: Any) -> Iterator[Dict[str, Any]]:
        """统一事件迭代器：重置本次结果 → 收集 → 触发 on_event → yield。

        run() 与 stream() 都走这里，保证两者都触发 on_event 并维护 last_usage/artifacts。
        ``stream_kwargs`` 透传给底层 ``NativeFloodAgent.stream``（如 abort_check / attachments）。
        """
        self._last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._artifacts = []
        for event in self._agent.stream(message, **stream_kwargs):
            authority = getattr(self._agent, "_journal_authority", None)
            if authority is not None:
                self._journal_authority = authority
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

    def stream(self, message: str, **stream_kwargs: Any) -> Iterator[Dict[str, Any]]:
        """流式执行，产出结构化事件 dict。

        ``**stream_kwargs`` 透传给底层 NativeFloodAgent.stream（如 ``abort_check``、
        ``attachments``），供宿主中断或传附件。

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
          - retry_attempt:  模型重试         {"type": "retry_attempt", "attempt": N, "error": "...", "delay": seconds}
          - wait:           重试前退避等待    {"type": "wait", "reason": "retry_backoff", "attempt": N, "duration": seconds}
          - recover:        重试成功后恢复    {"type": "recover", "attempt": N}
          - resume:         checkpoint 恢复  {"type": "resume", "checkpoint_id": "...", "status": "started|completed"}
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
        yield from self._iter(message, **stream_kwargs)

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
    def skill_registry(self) -> Any:
        """This Agent's isolated SkillRegistry."""
        return self._agent.skill_registry

    @property
    def raw(self) -> NativeFloodAgent:
        """访问底层 NativeFloodAgent 实例（高级用法）。"""
        return self._agent

    def __repr__(self) -> str:
        return f"<Agent tools={len(self._agent._orchestrator_registry.all())} session={self._agent.session_id}>"
