"""
ToolExecutionService — 统一工具执行管线

执行顺序固定为：
1. resolve tool from registry
2. set session context
3. permission check（PermissionService）
4. optional ASK（AskService）
5. validate input
6. execute tool
7. normalize ToolResult
8. emit action_end

设计原则：
- 唯一能调用工具函数的地方
- 工具函数不再自己做权限检查
- 工具函数只做业务逻辑和必要的输入保护
- ToolFeedback 统一由 service 生成
"""

import concurrent.futures
import contextvars
import inspect
import json
import logging
import queue
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.runtime.contracts.permissions import (
    PermissionAskRequest,
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    ToolFeedback,
    ValidationResult,
)
from floodmind.agent.runtime.contracts.paths import PathResolveRequest, PathResolveResult
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolExecutionContext, ToolResult, ToolSpec
from floodmind.agent.runtime.contracts.tool_transaction import canonical_arguments
from floodmind.agent.runtime.services.approval_fingerprint import compute_approval_fingerprint
from floodmind.agent.runtime.services.idempotency import (
    derive_idempotency_key,
    find_committed_result,
    side_effect_class_for_spec,
)
from floodmind.agent.runtime.services.tracing_service import TracingService

logger = logging.getLogger(__name__)


# Host-level 权限决策钩子签名：
#   (tool_name, tool_input, sdk_decision, permission_policy) -> PermissionDecision
# 在 SDK 完成基础权限判断后调用，允许宿主把 ALLOW 升级为 ASK/DENY、保留 SDK 的 DENY/ASK。
# 返回值必须是带 behavior 字段的 PermissionDecision（否则保留 SDK 原决策）。
PermissionDecisionHook = Callable[
    [str, Dict[str, Any], PermissionDecision, Optional[Any]],
    PermissionDecision,
]


class _ToolRunnerPool:
    """有界并发的工具执行器：每调用一个 daemon 线程 + 信号量限流（D-01）。

    旧实现是固定 8 worker 的共享队列：一个卡死的工具就永久占用一个 worker，
    8 个卡死即全进程所有会话的工具执行瘫痪直至重启。改为"每调用一线程 + 信号量限流"：

    - 并发上限 8，但提交时最多等待 ``QUEUE_WAIT_SECONDS`` 秒排队——并发满时新调用
      先排队等额度，等待超时才报 TOOL_EXECUTION_SATURATED（等效恢复旧的
      8 运行 + 32 排队缓冲语义，并行委派下不会轻易饱和）；
    - 超时的卡死线程被"遗弃"时立即归还并发额度（exactly-once 归还：遗弃方与
      线程结束方通过标志交接），宁可短暂超出软上限也不累积成进程级瘫痪；
    - daemon 线程不阻塞解释器退出。
    """

    QUEUE_WAIT_SECONDS = 10.0

    def __init__(self, max_concurrency: int = 8):
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._handoff_lock = threading.Lock()

    @property
    def max_concurrency(self) -> int:
        return self._sem._initial_value  # noqa: SLF001 - 同模块内部诊断

    @property
    def in_flight(self) -> int:
        """当前占用的并发额度（诊断用）。"""
        return self.max_concurrency - self._sem._value  # noqa: SLF001

    def submit(self, fn: Callable[[], Any]) -> "tuple[concurrent.futures.Future, Any]":
        """提交任务。排队等待超时/线程启动失败时抛 queue.Full（保持饱和语义）。

        Returns:
            (future, detach) —— detach 是超时遗弃时调用的回调（归还额度的 exactly-once 侧）。
        """
        if not self._sem.acquire(timeout=self.QUEUE_WAIT_SECONDS):
            raise queue.Full
        future: concurrent.futures.Future = concurrent.futures.Future()
        detached = [False]

        def _release_once() -> None:
            with self._handoff_lock:
                if detached[0]:
                    return False
                detached[0] = True
            self._sem.release()
            return True

        def _worker() -> None:
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(fn())
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                _release_once()

        try:
            threading.Thread(target=_worker, name="floodmind-tool-call", daemon=True).start()
        except Exception:
            # 线程资源耗尽等启动失败：立即归还额度，否则信号量槽位永久泄漏（P2-1）
            _release_once()
            raise queue.Full
        return future, _release_once


_TOOL_EXECUTOR = _ToolRunnerPool()


class ToolExecutionService:
    TOOL_TIMEOUT_SECONDS = 300

    def __init__(
        self,
        permission_service=None,
        path_service=None,
        ask_service=None,
        set_session_context_fn: Optional[Callable] = None,
        tracing_service: Optional[TracingService] = None,
        permission_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        permission_decision_hook: Optional[PermissionDecisionHook] = None,
    ):
        self._permission_service = permission_service
        self._path_service = path_service
        self._ask_service = ask_service
        self._set_session_context_fn = set_session_context_fn
        self._tracing_service = tracing_service
        # SDK 嵌入钩子：工具调用前同步回调 (tool_name, tool_input) -> bool，False 即拒绝。
        # 默认 None 不影响完整模式（走 permission_service）与 bare 模式（默认放行）。
        self._permission_handler = permission_handler
        # Host-level 权限决策钩子：SDK 基础判断后调用，可调整最终决策（见 PermissionDecisionHook）。
        # 异常时 fail-safe：保留 SDK 原决策。仅能收紧不能放开（DENY 不可被翻成 ALLOW/ASK）。
        self._permission_decision_hook = permission_decision_hook

    def execute(
        self,
        call: ToolCall,
        context: Optional[Any] = None,
        registry: Optional[Any] = None,
        authorized_ask_id: Optional[str] = None,
        *,
        journal_authority: Any,
        transaction_id: str = "",
        idempotency_key: str = "",
        side_effect_class: str = "read",
        canonical_arguments_str: str = "",
        arguments_sha256: str = "",
    ) -> ToolResult:
        """Execute a tool with services supplied by the execution RuntimeContext."""
        if journal_authority is None:
            raise ValueError("journal_authority is required for tool execution")
        return self._execute_bound(
            call,
            context,
            registry,
            authorized_ask_id,
            journal_authority=journal_authority,
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            side_effect_class=side_effect_class,
            canonical_arguments_str=canonical_arguments_str,
            arguments_sha256=arguments_sha256,
        )

    def _execute_bound(
        self,
        call: ToolCall,
        context: Optional[Any] = None,
        registry: Optional[Any] = None,
        authorized_ask_id: Optional[str] = None,
        *,
        journal_authority: Any,
        transaction_id: str = "",
        idempotency_key: str = "",
        side_effect_class: str = "read",
        canonical_arguments_str: str = "",
        arguments_sha256: str = "",
    ) -> ToolResult:
        if journal_authority is None:
            raise ValueError("journal_authority is required for tool execution")
        tool = self._resolve_tool(call, registry)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"未知工具: {call.name}",
                status="error",
            )

        # 统一参数键清洗（单点 chokepoint）：见 _sanitize_arguments docstring。
        # 在权限/校验/执行之前归一化，保证下游（权限、tracing、schema、**kwargs）都看到干净键。
        clean_arguments = self._sanitize_arguments(call.arguments)

        # ToolSpec.parameters 是工具对外暴露的完整 JSON Schema。必须在权限钩子和工具
        # handler 之前校验，避免无效输入触发授权流程或任何工具副作用。Pydantic
        # args_schema 仍在后续负责类型转换与生成最终 handler 参数。
        raw_schema_error = self._validate_raw_parameters(tool, clean_arguments)
        if raw_schema_error is not None:
            return self._make_input_validation_error(
                call,
                tool,
                clean_arguments,
                reason=raw_schema_error,
            )

        session_id = getattr(context, "session_id", "") if context else ""
        output_dir = getattr(context, "output_dir", "") if context else ""
        cwd = getattr(context, "cwd", "") if context else ""
        workspace_dir = getattr(context, "workspace_dir", "") if context else ""
        state_dir = getattr(context, "state_dir", "") if context else ""
        artifact_dir = getattr(context, "artifact_dir", "") if context else ""
        tmp_dir = getattr(context, "tmp_dir", "") if context else ""
        scripts_dir = getattr(context, "scripts_dir", "") if context else ""
        # 阶段C：子代理 delegate_cwd 经 SESSION_CONTEXT 注入，供 PathService 子代理写范围检查
        delegate_cwd = getattr(context, "delegate_cwd", "") if context else ""
        # 阶段D：agent 身份（主/子），阶段E：运行模式（规划/执行）
        agent_tier = getattr(context, "agent_tier", "main") if context else "main"
        mode = self._resolve_mode(context)
        runtime_context = getattr(context, "runtime_context", None) if context else None

        if self._set_session_context_fn is not None and session_id:
            self._invoke_session_context_callback(
                session_id,
                output_dir,
                delegate_cwd=delegate_cwd or None,
                cwd=cwd or None,
                workspace_dir=workspace_dir or None,
                state_dir=state_dir or None,
                artifact_dir=artifact_dir or None,
                tmp_dir=tmp_dir or None,
                scripts_dir=scripts_dir or None,
                runtime_context=runtime_context,
            )

        perm_input = dict(clean_arguments) if clean_arguments else {}
        perm_input["__call_id"] = call.id

        perm_decision = self._check_permissions(
            tool,
            perm_input,
            session_id,
            agent_tier,
            mode,
            journal_authority=journal_authority,
        )
        # Host-level 决策钩子：基础 SDK decision → hook → tracing 记录最终 decision → 执行，
        # 保证日志与实际行为一致。
        perm_decision = self._apply_permission_decision_hook(tool, perm_input, perm_decision)

        if self._tracing_service is not None:
            self._tracing_service.record_event(
                session_id,
                "permission",
                "permission_decision",
                input={"tool_name": tool.name, "call_id": call.id},
                output={"behavior": perm_decision.behavior.value, "reason": perm_decision.reason},
                status="error" if perm_decision.behavior == PermissionBehavior.DENY else "ok",
            )

        # 处理预授权 ask_id：用户已经通过 ASK 授权，直接执行
        if authorized_ask_id and perm_decision.behavior == PermissionBehavior.ASK:
            if self._ask_service is None:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content="授权服务未初始化，无法执行已授权操作。",
                    status="error",
                )
            approved = self._ask_service.wait_response(authorized_ask_id, timeout=0)
            if approved:
                # 跳过 ASK，改为 ALLOW
                perm_decision = PermissionDecision(behavior=PermissionBehavior.ALLOW, reason="用户已授权")
            else:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content="用户未授权或授权已超时。",
                    status="error",
                )

        # §6.6 事务中段事件（validated / permission.evaluated / approval.required /
        # execution.started）由 ToolExecutionService 在此处发出；executor 只发
        # tool.call.proposed 与终态事件。transaction_id 为空（旧调用方/测试未传）时
        # 全部跳过发射，保持向后兼容。approval_fingerprint 为确定性纯函数（§6.3），
        # 同一调用恒得同一指纹，绝不含时间/随机。
        canon = canonical_arguments(call.arguments)
        effective_canonical = canonical_arguments_str or canon
        approval_fingerprint = self._compute_approval_fingerprint(
            tool=tool,
            call=call,
            context=context,
            agent_tier=agent_tier,
            mode=mode,
            canonical_arguments_str=effective_canonical,
            side_effect_class=side_effect_class,
        )

        if perm_decision.behavior == PermissionBehavior.DENY:
            self._emit_permission_evaluated(
                journal_authority, transaction_id, call, "deny", approval_fingerprint
            )
            feedback = self._make_permission_feedback(perm_decision)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )
        if perm_decision.behavior == PermissionBehavior.ASK:
            # 非阻塞 ASK：发射事件，返回 awaiting_permission 状态
            if self._ask_service is None:
                feedback = self._make_permission_feedback(
                    PermissionDecision(behavior=PermissionBehavior.DENY, reason="授权服务未初始化")
                )
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=feedback.to_output_string(),
                    status="error",
                )
            self._emit_permission_evaluated(
                journal_authority, transaction_id, call, "ask", approval_fingerprint
            )
            if transaction_id:
                journal_authority.emit(
                    "tool.approval.required",
                    {
                        "transaction_id": transaction_id,
                        "call_id": call.id,
                        "tool_name": tool.name,
                        "reason": perm_decision.reason,
                        "arguments": effective_canonical,
                        "approval_fingerprint": approval_fingerprint,
                    },
                    call_id=call.id,
                )
            ask_id = self._ask_service.start_ask(
                PermissionAskRequest(
                    session_id=session_id,
                    call_id=call.id,
                    tool_name=tool.name,
                    reason=perm_decision.reason,
                    tool_input=perm_input,
                ),
                journal_authority=journal_authority,
            )
            if ask_id is None:
                feedback = self._make_permission_feedback(
                    PermissionDecision(behavior=PermissionBehavior.DENY, reason="无法发起用户确认")
                )
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=feedback.to_output_string(),
                    status="error",
                )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"等待用户确认: {perm_decision.reason}",
                status="awaiting_permission",
                metadata={"ask_id": ask_id, "reason": perm_decision.reason},
            )

        # exec 策略统一危险命令检查（单一可信来源，防止新 exec 工具遗漏自检）
        policy = getattr(tool, "permission_policy", None)
        if policy and getattr(policy, "policy_type", "") == "exec":
            command_field = getattr(policy, "command_field", "") or "command"
            command = str(perm_input.get(command_field, ""))
            if command and self._permission_service is not None:
                danger_decision = self._permission_service.check_dangerous_command(command)
                if danger_decision.behavior == PermissionBehavior.DENY:
                    self._emit_permission_evaluated(
                        journal_authority, transaction_id, call, "deny", approval_fingerprint
                    )
                    feedback = self._make_permission_feedback(danger_decision)
                    return ToolResult(
                        tool_call_id=call.id,
                        name=tool.name,
                        content=feedback.to_output_string(),
                        status="error",
                    )
            # P1-2：exec 未解析写目标的批准登记。凡最终决策走到 ALLOW（用户批准 /
            # 宿主预授权 / authorized_ask_id 授权重放），都要为执行层的
            # consume_unresolved_exec_write_approval 登记"精确命令串"批准——
            # 否则权限层放行、执行层拒绝，自相矛盾。
            self._register_unresolved_exec_write_approval(policy, perm_input)

        validation = tool.validate_input(clean_arguments)
        if hasattr(validation, "valid") and not validation.valid:
            args_preview = json.dumps(clean_arguments, ensure_ascii=False)[:500] if clean_arguments else "EMPTY"
            reason = getattr(validation, "reason", "")
            feedback = ToolFeedback(
                error_type="输入校验失败",
                error_code="INPUT_VALIDATION_FAILED",
                what_went_wrong=f"工具 {tool.name} 输入校验失败：{reason}。收到参数：{args_preview}",
                correct_usage="检查参数是否完整、格式是否正确，参考工具描述中的参数说明。",
                retryable=True,
                do_not_retry_same_call=False,
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )

        validated_args = self._validate_schema(tool, clean_arguments)
        if validated_args is None:
            args_preview = json.dumps(clean_arguments, ensure_ascii=False)[:500] if clean_arguments else "EMPTY"
            raw_hint = ""
            if hasattr(call, "_raw_arguments") and call._raw_arguments:
                ends_with_brace = call._raw_arguments.endswith("}")
                raw_hint = " 原始参数(JSON解析失败,长度=%d,末尾是'}'=%s): %s..." % (len(call._raw_arguments), ends_with_brace, call._raw_arguments[:200])
            feedback = ToolFeedback(
                error_type="输入校验失败",
                error_code="INPUT_VALIDATION_FAILED",
                what_went_wrong=f"工具 {tool.name} 参数校验失败。收到参数：{args_preview}{raw_hint}",
                correct_usage="检查参数名是否匹配、值类型是否正确。必填参数：参考工具描述中的 [必填] 标记。",
                retryable=True,
                do_not_retry_same_call=False,
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )

        # 校验通过后按 §6.6 链序发出 validated → permission.evaluated(allow)。
        # 权限决策此前已最终化（hook + exec-danger 检查），此处只是按 reducer 链序落记。
        if transaction_id:
            journal_authority.emit(
                "tool.call.validated",
                {"transaction_id": transaction_id, "call_id": call.id},
                call_id=call.id,
            )
            self._emit_permission_evaluated(
                journal_authority, transaction_id, call, "allow", approval_fingerprint
            )

        # §6.5 幂等：非 read 且有幂等键，先查已提交结果，命中直接复用（不重执行）。
        # 与 executor 的 tool.call.proposed 使用同一 canonical 形态（call.arguments），
        # 保证幂等键一致；dummy journal_authority（无 read_after）跳过查询。
        # 幂等短路在本处之前 return，因此重放不会发出 execution.started（不重执行）。
        # 幂等键单一来源（D-04）：executor 的 tool.call.proposed 已派生并透传时直接使用，
        # 避免本地二次派生与 proposed 事件分叉；仅在直接调用（键为空）时本地派生。
        if not idempotency_key:
            side_effect_class = side_effect_class_for_spec(tool)
            idempotency_key = derive_idempotency_key(
                tool_id=call.name,
                canonical_arguments=canon,
                side_effect_class=side_effect_class,
            )
        if idempotency_key and hasattr(journal_authority, "read_after"):
            committed = find_committed_result(journal_authority, idempotency_key)
            if committed is not None:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=committed["result_summary"],
                    status="completed",
                    artifacts=committed["artifacts"],
                    metadata={"idempotent_replay": True, "full_ref": committed["full_ref"]},
                )

        # 真正副作用前发出 started：此后才把事务标记 running（reducer 链）。
        if transaction_id:
            journal_authority.emit(
                "tool.execution.started",
                {
                    "transaction_id": transaction_id,
                    "call_id": call.id,
                    "tool_id": call.name,
                    "arguments": effective_canonical,
                },
                call_id=call.id,
            )

        try:
            ctx = contextvars.copy_context()
            try:
                future, detach_slot = _TOOL_EXECUTOR.submit(lambda: ctx.run(tool.func, **validated_args))
            except queue.Full:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        f"工具执行并发已满（{_TOOL_EXECUTOR.in_flight}/{_TOOL_EXECUTOR.max_concurrency}），请稍后重试。"
                    ),
                    status="error",
                    metadata={
                        "error_code": "TOOL_EXECUTION_SATURATED",
                        "retryable": True,
                        "in_flight": _TOOL_EXECUTOR.in_flight,
                        "max_concurrency": _TOOL_EXECUTOR.max_concurrency,
                    },
                )
            try:
                output = future.result(timeout=self.TOOL_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                # 每调用一线程：任务提交即开跑，超时时 future 必为 RUNNING，取消不可达。
                # 卡死线程遗弃：立即归还并发额度（D-01），避免累积成进程级工具执行瘫痪。
                detach_slot()
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        f"工具执行超过{self.TOOL_TIMEOUT_SECONDS}秒；运行线程无法安全停止，"
                        "操作结果不确定，且可能仍在后台完成。请先核实外部状态，不要自动重试。"
                    ),
                    status="error",
                    metadata={
                        "error_code": "TOOL_EXECUTION_TIMEOUT_INDETERMINATE",
                        "timeout_seconds": self.TOOL_TIMEOUT_SECONDS,
                        "execution_state": "indeterminate_running",
                        "indeterminate": True,
                        "cancelled": False,
                        "retryable": False,
                        "do_not_retry_same_call": True,
                    },
                )
            output_str = str(output) if output is not None else ""
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=output_str,
                status="completed",
            )
        except Exception as exc:
            logger.error("ToolExecutionService tool %s execution error: %s", call.name, exc, exc_info=True)
            err_msg = str(exc)
            # 防御纵深：即便键清洗后仍有未知键（模型把键名写错），明示"可能有多余引号/空白"
            # 让模型能理解并自纠，而不是收到看不懂的 TypeError 原文。
            correct_usage = "检查参数是否正确，或查看工具文档。"
            if "unexpected keyword argument" in err_msg:
                correct_usage = (
                    "参数名可能有多余引号/空白（模型偶发畸形键名）。请重新生成参数，"
                    "确保参数名与工具 schema 完全一致，不要带引号或多余字符。"
                )
            feedback = ToolFeedback(
                error_type="执行失败",
                error_code="TOOL_EXECUTION_ERROR",
                what_went_wrong=err_msg,
                correct_usage=correct_usage,
                retryable=True,
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )

    def _invoke_session_context_callback(
        self,
        session_id: str,
        output_dir: str,
        **session_fields: Any,
    ) -> None:
        """Invoke the callback once, adapting arguments before user code runs.

        Signature inspection distinguishes legacy callbacks from modern callbacks. A
        ``TypeError`` raised inside callback code therefore propagates normally instead
        of being mistaken for a signature mismatch and triggering duplicate side effects.
        """
        callback = self._set_session_context_fn
        if callback is None:
            return

        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            # Some extension callables have no introspectable signature. Use the modern
            # contract once; never retry after execution based on exception type.
            callback(session_id, output_dir, **session_fields)
            return

        parameters = signature.parameters.values()
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters)
        if accepts_kwargs:
            supported_fields = session_fields
        else:
            supported_names = {
                param.name
                for param in parameters
                if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            }
            supported_fields = {
                name: value for name, value in session_fields.items() if name in supported_names
            }

        # bind() detects genuinely incompatible callbacks before any callback code runs.
        signature.bind(session_id, output_dir, **supported_fields)
        callback(session_id, output_dir, **supported_fields)

    def _resolve_tool(self, call: ToolCall, registry: Optional[Any]) -> Optional[ToolSpec]:
        if registry is None:
            return None
        tool = registry.get(call.name)
        return tool

    @staticmethod
    def _resolve_mode(context: Any) -> str:
        """从 context 取 mode（阶段E：规划/执行硬门）。

        子代理恒 execution；主代理可能为 planning。
        """
        if context is None:
            return "execution"
        # RunContext.agent_tier="sub" → 恒 execution
        tier = getattr(context, "agent_tier", "")
        if tier == "sub":
            return "execution"
        # 显式 mode 字段 > 从 AgentLoopState 发现（未来可由状态机驱动）
        mode = getattr(context, "mode", "")
        if mode:
            return mode
        return "execution"

    def _compute_approval_fingerprint(
        self,
        *,
        tool: ToolSpec,
        call: ToolCall,
        context: Any,
        agent_tier: str,
        mode: str,
        canonical_arguments_str: str,
        side_effect_class: str,
    ) -> str:
        """§6.3 确定性 approval fingerprint（纯函数，无 I/O/时间/随机）。

        full target resolution is a follow-up —— resolved_targets=[]；workspace_generation
        由 workspace_dir 确定性派生（绝不用时间/随机），同一调用恒得同一指纹。
        """
        cwd = getattr(context, "cwd", "") if context else ""
        workspace_dir = getattr(context, "workspace_dir", "") if context else ""
        return compute_approval_fingerprint(
            tool_id=tool.name,
            tool_version="1",
            canonical_arguments=canonical_arguments_str or canonical_arguments(call.arguments),
            resolved_targets=[],
            cwd=cwd or "",
            environment_identity="",
            workspace_id=workspace_dir or "",
            workspace_generation=f"{workspace_dir}#v1" if workspace_dir else "",
            sandbox_permissions=[],
            agent_tier=agent_tier or "main",
            runtime_mode=mode or "execution",
            side_effect_class=side_effect_class or "read",
            policy_version="v1",
        )

    @staticmethod
    def _emit_permission_evaluated(
        journal_authority: Any,
        transaction_id: str,
        call: ToolCall,
        decision: str,
        approval_fingerprint: str,
    ) -> None:
        """发 tool.permission.evaluated（无事务 id 时为 no-op，兼容旧调用方）。"""
        if not transaction_id:
            return
        journal_authority.emit(
            "tool.permission.evaluated",
            {
                "transaction_id": transaction_id,
                "call_id": call.id,
                "decision": decision,
                "approval_fingerprint": approval_fingerprint or "",
            },
            call_id=call.id,
        )

    def _check_permissions(
        self,
        tool: ToolSpec,
        perm_input: Dict[str, Any],
        session_id: str,
        agent_tier: str = "main",
        mode: str = "execution",
        *,
        journal_authority: Any,
    ) -> PermissionDecision:
        if journal_authority is None:
            raise ValueError("journal_authority is required for permission checks")
        # SDK permission_handler 钩子（宿主预授权）：True = 宿主同意执行，可满足策略级 ASK
        #（跳过用户交互确认），但不可翻越 SDK 安全硬门——tier / planning / 路径 / 危险命令 /
        # 全局 deny 规则照常生效（钩子只能收紧不能放开，与 permission_decision_hook 语义对齐）；
        # False = 宿主拒绝 → DENY；None = 宿主无意见 → 交给 SDK 正常判断（含 ASK 交互）。
        host_preapproved = False
        if self._permission_handler is not None:
            clean_input = {k: v for k, v in perm_input.items() if k != "__call_id"}
            try:
                approved = self._permission_handler(tool.name, clean_input)
            except Exception as e:
                # 钩子异常视为宿主无意见（交给 SDK 判断），不放大放行权限——钩子只能收紧不能放开。
                logger.warning("permission_handler 执行异常（按无意见处理，交给 SDK 判断）: %s", e)
                approved = None
            if approved is True:
                host_preapproved = True
            elif approved is False:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    reason=f"permission_handler 拒绝了工具 {tool.name} 的调用",
                )
            # approved is None（或非 bool 值）→ 继续 SDK 判断

        if self._permission_service is not None:
            request = PermissionRequest(
                session_id=session_id,
                call_id=str(perm_input.get("__call_id", "")),
                tool_name=tool.name,
                tool_input=perm_input,
                permission_policy=getattr(tool, "permission_policy", None),
                is_readonly=bool(getattr(tool, "is_readonly", False)),
                agent_tier=agent_tier,
                mode=mode,
            )
            check_fn = getattr(tool, "check_permissions_fn", None)
            if check_fn is not None:
                request._check_permissions_fn = check_fn
            return self._permission_service.check(
                request,
                journal_authority=journal_authority,
                host_preapproved=host_preapproved,
            )

        result = tool.check_permissions(perm_input)
        if hasattr(result, "behavior"):
            behavior = result.behavior
            if isinstance(behavior, PermissionBehavior):
                if behavior == PermissionBehavior.ASK and host_preapproved:
                    return PermissionDecision(
                        behavior=PermissionBehavior.ALLOW,
                        reason="宿主 permission_handler 预授权，跳过用户确认",
                    )
                return PermissionDecision(behavior=behavior, reason=getattr(result, "reason", ""))
            # 决策对象不可识别（如测试桩）：预授权放行，否则 fail-closed。
            if host_preapproved:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    reason="宿主 permission_handler 预授权（决策对象不可识别）",
                )
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason="权限决策对象不可识别且无宿主预授权（fail-closed）",
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    # 行为严格度排序：用于保证 hook 只能收紧决策、不能放开。
    _BEHAVIOR_SEVERITY = {
        PermissionBehavior.ALLOW: 0,
        PermissionBehavior.ASK: 1,
        PermissionBehavior.DENY: 2,
    }

    def _apply_permission_decision_hook(
        self,
        tool: ToolSpec,
        perm_input: Dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """应用 host-level 权限决策钩子（在 SDK 基础判断之后）。

        hook 收到 SDK 原始 decision 与工具 permission_policy，可返回调整后的决策。
        约束：
        - 只能收紧，不能放开：DENY 不可被改成 ALLOW/ASK，ASK 不可被改成 ALLOW，
          防止宿主绕过 SDK 的 path / 危险命令 / 子代理分层 / planning 模式等安全判断。
        - hook 抛异常或返回非法值时 fail-safe：保留 SDK 原决策。
        """
        if self._permission_decision_hook is None:
            return decision

        clean_input = {k: v for k, v in perm_input.items() if k != "__call_id"}
        policy = getattr(tool, "permission_policy", None)

        try:
            next_decision = self._permission_decision_hook(tool.name, clean_input, decision, policy)
        except Exception as e:
            logger.warning("permission_decision_hook 执行异常（保留 SDK 原决策）: %s", e)
            return decision

        if next_decision is None or not hasattr(next_decision, "behavior"):
            logger.warning(
                "permission_decision_hook 返回非法决策（保留 SDK 原决策）: %r", next_decision
            )
            return decision

        behavior = next_decision.behavior
        if not isinstance(behavior, PermissionBehavior):
            try:
                behavior = PermissionBehavior(behavior)
            except (ValueError, KeyError):
                logger.warning(
                    "permission_decision_hook 返回未知 behavior（保留 SDK 原决策）: %r", behavior
                )
                return decision

        if self._BEHAVIOR_SEVERITY[behavior] < self._BEHAVIOR_SEVERITY[decision.behavior]:
            logger.warning(
                "permission_decision_hook 试图放宽 SDK 决策（%s -> %s），已忽略",
                decision.behavior.value,
                behavior.value,
            )
            return decision

        return PermissionDecision(behavior=behavior, reason=getattr(next_decision, "reason", ""))

    def _register_unresolved_exec_write_approval(self, policy: Any, perm_input: Dict[str, Any]) -> None:
        """exec ALLOW 路径统一登记"未解析写目标"批准（与执行层按命令串一次性消费配对）。

        归一化与 PermissionService._handle_ask 的登记保持同源，避免批准串与消费串
        因去引号/JSON 解包差异而不一致。失败仅告警：执行层消费不到批准时按既有
        契约 fail-closed 拒绝，不会放大权限。
        """
        try:
            from floodmind.agent.runtime.services.exec_write_scanner import (
                approve_unresolved_exec_writes,
                scan_exec_writes,
            )

            command_field = getattr(policy, "command_field", "") or "command"
            command = str(perm_input.get(command_field, "") or "").strip()
            if not command:
                return
            if self._permission_service is not None:
                normalized = self._permission_service._normalize_tool_input(dict(perm_input))
                command = str(normalized.get(command_field, "") or "").strip()
            if command and scan_exec_writes(command).unresolved:
                approve_unresolved_exec_writes(command)
        except Exception as exc:
            logger.warning("登记 exec 未解析写目标批准失败（执行层将 fail-closed 兜底）: %s", exc)

    def _make_permission_feedback(self, decision: PermissionDecision) -> ToolFeedback:
        if self._permission_service is not None:
            return self._permission_service.make_feedback(decision)

        if decision.behavior == PermissionBehavior.DENY:
            return ToolFeedback(
                error_type="权限拒绝",
                error_code="PERMISSION_DENIED",
                what_went_wrong=decision.reason,
                correct_usage="检查路径是否在允许目录内，或确认操作是否需要用户授权。",
                retryable=False,
                do_not_retry_same_call=True,
            )
        return ToolFeedback(
            error_type="权限拒绝",
            error_code="PERMISSION_ASK_DENIED",
            what_went_wrong=f"需要用户确认: {decision.reason}",
            correct_usage="此操作需要用户授权，当前自动拒绝。请换一种不需要授权的方式，或向用户说明原因。",
            retryable=False,
            do_not_retry_same_call=True,
        )

    @staticmethod
    def _sanitize_arguments(arguments: Optional[dict]) -> dict:
        """统一清洗模型生成的工具调用参数键名（MiniMax-M3 等偶发畸形键）。

        背景：模型偶发生成键名带尾引号/首尾空白/控制字符的参数（如
        ``{"tool_name"": ...}``）。对无 pydantic args_schema 的裸 JSON Schema 工具
        （GetTool、系统工具、MCP 工具），旧逻辑原样放行后 ``**kwargs``
        直接崩成 ``TypeError: unexpected keyword argument 'tool_name"'``，模型看不懂
        不会自纠。这里在权限/校验/执行之前统一归一化键名：去边缘引号/空白、去键内
        控制字符与引号、丢弃空键。只改键名，不改值、不丢合法参数。
        """
        if not isinstance(arguments, dict):
            return dict(arguments) if arguments else {}
        cleaned: Dict[str, Any] = {}
        for key, value in arguments.items():
            if not isinstance(key, str):
                cleaned[key] = value
                continue
            clean_key = key.strip().strip('"').strip("'").strip()
            # 键内残留引号与控制字符一并去除（合法键名恒为标识符，引号/控制符出现必为模型错误）
            clean_key = re.sub(r"[\x00-\x1f\x7f\"']", "", clean_key).strip()
            if not clean_key:
                continue
            cleaned[clean_key] = value
        return cleaned

    @staticmethod
    def _validate_raw_parameters(tool: ToolSpec, arguments: dict) -> Optional[str]:
        """Validate sanitized arguments against the complete ToolSpec JSON Schema.

        ``args_schema`` only covers tools backed by Pydantic models. Raw/MCP/system
        tools rely on ``parameters`` and may use JSON Schema features that Pydantic
        does not enforce, including composition and references.
        """
        schema = getattr(tool, "parameters", None)
        if not isinstance(schema, dict):
            return None

        try:
            from jsonschema import exceptions, validators

            validator_class = validators.validator_for(schema)
            validator_class.check_schema(schema)
            errors = sorted(
                validator_class(schema).iter_errors(arguments),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
        except exceptions.SchemaError as exc:
            logger.warning(
                "ToolExecutionService invalid parameters schema for %s: %s",
                tool.name,
                exc,
            )
            return f"工具参数 schema 无效：{exc.message}"
        except Exception as exc:
            logger.warning(
                "ToolExecutionService JSON Schema validation error for %s: %s",
                tool.name,
                exc,
            )
            return f"JSON Schema 校验异常：{exc}"

        if not errors:
            return None

        details = []
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"{location}: {error.message}")
        reason = "; ".join(details)
        logger.warning(
            "ToolExecutionService parameters validation failed for %s: %s",
            tool.name,
            reason,
        )
        return reason

    @staticmethod
    def _make_input_validation_error(
        call: ToolCall,
        tool: ToolSpec,
        arguments: dict,
        reason: str,
    ) -> ToolResult:
        args_preview = json.dumps(arguments, ensure_ascii=False)[:500] if arguments else "EMPTY"
        feedback = ToolFeedback(
            error_type="输入校验失败",
            error_code="INPUT_VALIDATION_FAILED",
            what_went_wrong=f"工具 {tool.name} 输入校验失败：{reason}。收到参数：{args_preview}",
            correct_usage="检查参数是否完整、参数名和值类型是否符合工具的 JSON Schema。",
            retryable=True,
            do_not_retry_same_call=False,
        )
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=feedback.to_output_string(),
            status="error",
        )

    def _validate_schema(self, tool: ToolSpec, arguments: dict) -> Optional[dict]:
        schema = getattr(tool, "args_schema", None)
        if schema is None:
            return dict(arguments) if arguments else {}
        try:
            from pydantic import BaseModel, ValidationError
            if not issubclass(schema, BaseModel):
                return dict(arguments) if arguments else {}
            validated = schema.model_validate(arguments)
            return validated.model_dump()
        except ValidationError as e:
            missing_fields = [str(err["loc"][0]) for err in e.errors() if err["type"] == "missing"]
            type_errors = [f"{'.'.join(str(x) for x in err['loc'])}: 期望={err.get('expected','?')}, 收到={err.get('received','?')}" for err in e.errors() if err["type"] != "missing"]
            extra_fields = ['.'.join(str(x) for x in err['loc']) for err in e.errors() if err["type"] == "extra_forbidden"]
            details = []
            if missing_fields:
                details.append(f"缺少字段: {missing_fields}")
            if type_errors:
                details.append(f"类型/格式错误: {type_errors}")
            if extra_fields:
                details.append(f"多余字段: {extra_fields}")
            args_preview = json.dumps(arguments, ensure_ascii=False)[:500] if arguments else "EMPTY"
            logger.warning("ToolExecutionService schema validation failed for %s: %s. Details: %s. Received: %s", tool.name, e, "; ".join(details), args_preview)
            return None
        except Exception as e:
            logger.warning("ToolExecutionService schema validation error for %s: %s", tool.name, e)
            return dict(arguments) if arguments else {}
