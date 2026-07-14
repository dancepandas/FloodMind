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

import json
import logging
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
from floodmind.agent.runtime.services.tracing_service import TracingService

logger = logging.getLogger(__name__)


class ToolExecutionService:
    def __init__(
        self,
        permission_service=None,
        path_service=None,
        ask_service=None,
        set_session_context_fn: Optional[Callable] = None,
        tracing_service: Optional[TracingService] = None,
        permission_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    ):
        self._permission_service = permission_service
        self._path_service = path_service
        self._ask_service = ask_service
        self._set_session_context_fn = set_session_context_fn
        self._tracing_service = tracing_service
        # SDK 嵌入钩子：工具调用前同步回调 (tool_name, tool_input) -> bool，False 即拒绝。
        # 默认 None 不影响完整模式（走 permission_service）与 bare 模式（默认放行）。
        self._permission_handler = permission_handler

    def execute(
        self,
        call: ToolCall,
        context: Optional[Any] = None,
        registry: Optional[Any] = None,
        authorized_ask_id: Optional[str] = None,
    ) -> ToolResult:
        tool = self._resolve_tool(call, registry)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"未知工具: {call.name}",
                status="error",
            )

        session_id = getattr(context, "session_id", "") if context else ""
        output_dir = getattr(context, "output_dir", "") if context else ""
        # 阶段C：子代理 delegate_cwd 经 SESSION_CONTEXT 注入，供 PathService 子代理写范围检查
        delegate_cwd = getattr(context, "delegate_cwd", "") if context else ""
        # 阶段D：agent 身份（主/子），阶段E：运行模式（规划/执行）
        agent_tier = getattr(context, "agent_tier", "main") if context else "main"
        mode = self._resolve_mode(context)

        if self._set_session_context_fn is not None and session_id:
            try:
                self._set_session_context_fn(session_id, output_dir, delegate_cwd=delegate_cwd or None)
            except TypeError:
                # 回调签名不支持 delegate_cwd（旧签名），降级兼容
                self._set_session_context_fn(session_id, output_dir)

        perm_input = dict(call.arguments) if call.arguments else {}
        perm_input["__call_id"] = call.id

        perm_decision = self._check_permissions(tool, perm_input, session_id, agent_tier, mode)

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

        if perm_decision.behavior == PermissionBehavior.DENY:
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
            ask_id = self._ask_service.start_ask(
                PermissionAskRequest(
                    session_id=session_id,
                    call_id=call.id,
                    tool_name=tool.name,
                    reason=perm_decision.reason,
                    tool_input=perm_input,
                )
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
                    feedback = self._make_permission_feedback(danger_decision)
                    return ToolResult(
                        tool_call_id=call.id,
                        name=tool.name,
                        content=feedback.to_output_string(),
                        status="error",
                    )

        validation = tool.validate_input(call.arguments)
        if hasattr(validation, "valid") and not validation.valid:
            args_preview = json.dumps(call.arguments, ensure_ascii=False)[:500] if call.arguments else "EMPTY"
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

        validated_args = self._validate_schema(tool, call.arguments)
        if validated_args is None:
            args_preview = json.dumps(call.arguments, ensure_ascii=False)[:500] if call.arguments else "EMPTY"
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

        try:
            import concurrent.futures
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(tool.func, **validated_args)
            try:
                output = future.result(timeout=300)
            except concurrent.futures.TimeoutError:
                executor.shutdown(wait=False)
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content="工具执行超时（300秒）",
                    status="error",
                )
            finally:
                executor.shutdown(wait=False)
            output_str = str(output) if output is not None else ""
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=output_str,
                status="completed",
            )
        except Exception as exc:
            logger.error("ToolExecutionService tool %s execution error: %s", call.name, exc, exc_info=True)
            feedback = ToolFeedback(
                error_type="执行失败",
                error_code="TOOL_EXECUTION_ERROR",
                what_went_wrong=str(exc),
                correct_usage="检查参数是否正确，或查看工具文档。",
                retryable=True,
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )

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

    def _check_permissions(
        self,
        tool: ToolSpec,
        perm_input: Dict[str, Any],
        session_id: str,
        agent_tier: str = "main",
        mode: str = "execution",
    ) -> PermissionDecision:
        # SDK permission_handler 钩子（最高优先级）：嵌入方可同步审批/拦截，无需 permission_service。
        if self._permission_handler is not None:
            clean_input = {k: v for k, v in perm_input.items() if k != "__call_id"}
            try:
                approved = self._permission_handler(tool.name, clean_input)
            except Exception as e:
                logger.warning("permission_handler 执行异常（按放行处理）: %s", e)
                approved = True
            if not approved:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    reason=f"permission_handler 拒绝了工具 {tool.name} 的调用",
                )

        if self._permission_service is not None:
            request = PermissionRequest(
                session_id=session_id,
                call_id=str(perm_input.get("__call_id", "")),
                tool_name=tool.name,
                tool_input=perm_input,
                permission_policy=getattr(tool, "permission_policy", None),
                agent_tier=agent_tier,
                mode=mode,
            )
            check_fn = getattr(tool, "check_permissions_fn", None)
            if check_fn is not None:
                request._check_permissions_fn = check_fn
            return self._permission_service.check(request)

        result = tool.check_permissions(perm_input)
        if hasattr(result, "behavior"):
            return PermissionDecision(behavior=result.behavior, reason=getattr(result, "reason", ""))
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

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
