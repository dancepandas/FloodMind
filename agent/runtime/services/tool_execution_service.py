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

from agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    ToolFeedback,
    ValidationResult,
)
from agent.runtime.contracts.paths import PathResolveRequest, PathResolveResult
from agent.runtime.contracts.tools import ToolCall, ToolExecutionContext, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class ToolExecutionService:
    def __init__(
        self,
        permission_service=None,
        path_service=None,
        ask_service=None,
        set_session_context_fn: Optional[Callable] = None,
    ):
        self._permission_service = permission_service
        self._path_service = path_service
        self._ask_service = ask_service
        self._set_session_context_fn = set_session_context_fn

    def execute(self, call: ToolCall, context: Optional[Any] = None, registry: Optional[Any] = None) -> ToolResult:
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

        if self._set_session_context_fn is not None and session_id:
            self._set_session_context_fn(session_id, output_dir)

        perm_input = dict(call.arguments) if call.arguments else {}
        perm_input["__call_id"] = call.id

        perm_decision = self._check_permissions(tool, perm_input, session_id)
        if perm_decision.behavior == PermissionBehavior.DENY:
            feedback = self._make_permission_feedback(perm_decision)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=feedback.to_output_string(),
                status="error",
            )
        if perm_decision.behavior == PermissionBehavior.ASK:
            feedback = self._make_permission_feedback(perm_decision)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
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
            output = tool.func(**validated_args)
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

    def _check_permissions(self, tool: ToolSpec, perm_input: Dict[str, Any], session_id: str) -> PermissionDecision:
        if self._permission_service is not None:
            request = PermissionRequest(
                session_id=session_id,
                call_id=str(perm_input.get("__call_id", "")),
                tool_name=tool.name,
                tool_input=perm_input,
                permission_policy=getattr(tool, "permission_policy", None),
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
