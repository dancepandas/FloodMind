"""
Structured Error Classification — 结构化错误分类与恢复策略

借鉴 Hermes agent/error_classifier.py，针对 FloodMind 水文场景定制。
将散落的错误处理统一为：分类 → 策略 → 执行。
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class HydroErrorType(enum.Enum):
    """水文领域错误分类体系"""

    # 数据层
    data_format = "data_format"          # CSV/Excel 格式不对、列缺失、编码错误
    data_range = "data_range"            # 数据超出模型有效范围（时间范围、数值范围）
    data_missing = "data_missing"        # 必填数据缺失

    # 模型层
    model_param = "model_param"          # 参数越界或类型错误
    model_runtime = "model_runtime"      # 模型执行出错（依赖缺失、内存不足、崩溃）
    model_not_found = "model_not_found"  # 请求的模型/配置不存在

    # API / 外部服务
    api_timeout = "api_timeout"          # 外部 API 连接/读取超时
    api_rate_limit = "api_rate_limit"    # API 限流
    api_auth = "api_auth"                # API 认证失败
    api_billing = "api_billing"          # API 余额不足/欠费

    # 上下文 / 负载
    context_overflow = "context_overflow"  # 上下文超限
    payload_too_large = "payload_too_large"  # 请求体过大

    # 基础设施
    file_permission = "file_permission"  # 文件读写权限
    network = "network"                  # 网络连接问题
    resource_exhausted = "resource_exhausted"  # 计算资源不足（CPU/内存/磁盘）

    # 未知
    unknown = "unknown"


@dataclass
class RecoveryAction:
    """恢复动作"""

    action: str  # retry / retry_with_fix / fallback / compress / ask_user / abort
    fix_hint: str = ""           # 给 LLM 的修正提示
    fallback_model: str = ""     # 降级模型
    backoff_seconds: float = 0.0 # 退避时间
    max_retries: int = 0         # 此动作下的最大重试次数
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifiedError:
    """分类后的错误"""

    error_type: HydroErrorType
    message: str = ""
    original_error: str = ""
    recovery: RecoveryAction = field(default_factory=lambda: RecoveryAction(action="abort"))


# ── 错误模式匹配规则 ──────────────────────────────────────────────
# 优先级：从上到下匹配，先匹配的先生效
_ERROR_PATTERNS: list[tuple[HydroErrorType, list[str], RecoveryAction]] = [
    # 数据格式
    (
        HydroErrorType.data_format,
        ["格式错误", "编码错误", "无法解析", "invalid format", "parse error",
         "encoding", "utf-8", "列不存在", "column not found", "header mismatch"],
        RecoveryAction(action="retry_with_fix", fix_hint="请检查数据文件格式（CSV 分隔符、编码、列名），必要时先调用数据清洗工具。", max_retries=2),
    ),
    # 数据范围
    (
        HydroErrorType.data_range,
        ["超出范围", "时间范围", "数值越界", "out of range", "exceeds",
         "invalid date range", "data span too short", "insufficient data"],
        RecoveryAction(action="retry_with_fix", fix_hint="请检查数据时间范围或数值范围是否满足模型要求。", max_retries=1),
    ),
    # 模型参数
    (
        HydroErrorType.model_param,
        ["参数错误", "参数越界", "invalid parameter", "parameter out of range",
         "bad argument", "validation failed", "config error"],
        RecoveryAction(action="retry_with_fix", fix_hint="请修正模型参数，参考工具描述中的有效范围。", max_retries=2),
    ),
    # 模型运行时错误
    (
        HydroErrorType.model_runtime,
        ["模型执行失败", "运行时错误", "runtime error", "execution failed",
         "segmentation fault", "core dumped", "依赖缺失", "module not found"],
        RecoveryAction(action="fallback", fix_hint="模型执行环境异常，尝试使用备用模型。", max_retries=0),
    ),
    # API 认证
    (
        HydroErrorType.api_auth,
        ["认证失败", "401", "403", "Unauthorized", "Forbidden",
         "invalid api key", "auth failed", "signature error"],
        RecoveryAction(action="abort", fix_hint="API 认证失败，请检查 API Key 配置。", max_retries=0),
    ),
    # API 余额
    (
        HydroErrorType.api_billing,
        ["余额不足", "欠费", "quota exceeded", "insufficient quota",
         "billing", "payment required", "402", "Arrearage", "QuotaExhausted"],
        RecoveryAction(action="abort", fix_hint="API 余额不足，请充值或切换模型。", max_retries=0),
    ),
    # API 限流
    (
        HydroErrorType.api_rate_limit,
        ["限流", "429", "Too Many Requests", "rate limit", "throttled",
         "请求过于频繁"],
        RecoveryAction(action="retry", backoff_seconds=2.0, max_retries=3),
    ),
    # 超时
    (
        HydroErrorType.api_timeout,
        ["超时", "timeout", "timed out", "ReadTimeout", "ConnectTimeout",
         "deadline exceeded", "connection timed out"],
        RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=2),
    ),
    # 上下文溢出
    (
        HydroErrorType.context_overflow,
        ["上下文过长", "token limit", "context length", "too long",
         "maximum context", "exceeds max token", "413"],
        RecoveryAction(action="compress", fix_hint="上下文超出限制，将触发自动压缩。", max_retries=1),
    ),
    # 文件权限
    (
        HydroErrorType.file_permission,
        ["权限拒绝", "Permission denied", "access denied", "无权访问",
         "read-only", "cannot write"],
        RecoveryAction(action="ask_user", fix_hint="文件权限不足，需要用户确认或修改权限。", max_retries=0),
    ),
    # 网络
    (
        HydroErrorType.network,
        ["网络错误", "connection error", "network unreachable", "dns",
         "refused", "reset by peer"],
        RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=2),
    ),
    # 资源耗尽
    (
        HydroErrorType.resource_exhausted,
        ["内存不足", "磁盘已满", "out of memory", "no space left",
         "resource temporarily unavailable", "cannot allocate"],
        RecoveryAction(action="abort", fix_hint="系统资源不足，请清理后重试。", max_retries=0),
    ),
]


class ErrorClassifier:
    """错误分类器"""

    @classmethod
    def classify(cls, error: Exception, context: Optional[Dict[str, Any]] = None) -> ClassifiedError:
        """
        对异常进行分类，返回分类结果和恢复策略。

        Args:
            error: 捕获的异常对象
            context: 可选上下文（如 tool_name, model_key 等）
        """
        error_str = str(error)
        error_type = type(error).__name__
        full_text = f"{error_type}: {error_str}".lower()

        # 1. 按模式匹配
        for hydro_type, patterns, recovery in _ERROR_PATTERNS:
            for pattern in patterns:
                if pattern.lower() in full_text:
                    return ClassifiedError(
                        error_type=hydro_type,
                        message=f"检测到 {hydro_type.value}: {error_str[:200]}",
                        original_error=error_str,
                        recovery=recovery,
                    )

        # 2. 按异常类型匹配（子类在前，避免被父类抢先）
        type_mapping = {
            "TimeoutError": (HydroErrorType.api_timeout, RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=2)),
            "ReadTimeout": (HydroErrorType.api_timeout, RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=2)),
            "ConnectionError": (HydroErrorType.network, RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=2)),
            "FileNotFoundError": (HydroErrorType.data_missing, RecoveryAction(action="ask_user", fix_hint="文件不存在，请检查路径或上传文件。", max_retries=0)),
            "PermissionError": (HydroErrorType.file_permission, RecoveryAction(action="ask_user", max_retries=0)),
            "MemoryError": (HydroErrorType.resource_exhausted, RecoveryAction(action="abort", max_retries=0)),
            "OSError": (HydroErrorType.resource_exhausted, RecoveryAction(action="abort", max_retries=0)),
            "JSONDecodeError": (HydroErrorType.data_format, RecoveryAction(action="retry_with_fix", fix_hint="JSON 格式错误，尝试修复输入数据。", max_retries=2)),
            "ValueError": (HydroErrorType.data_range, RecoveryAction(action="retry_with_fix", fix_hint="参数值无效，检查数据范围。", max_retries=2)),
        }

        for exc_type, (hydro_type, recovery) in type_mapping.items():
            if exc_type in error_type or isinstance(error, __import__("builtins").getattr(__import__("builtins"), exc_type, type)):
                return ClassifiedError(
                    error_type=hydro_type,
                    message=f"检测到 {hydro_type.value}: {error_str[:200]}",
                    original_error=error_str,
                    recovery=recovery,
                )

        # 3. 未知错误
        return ClassifiedError(
            error_type=HydroErrorType.unknown,
            message=f"未分类错误: {error_str[:200]}",
            original_error=error_str,
            recovery=RecoveryAction(action="retry", backoff_seconds=1.0, max_retries=1),
        )

    @classmethod
    def classify_tool_error(
        cls,
        tool_name: str,
        error: Exception,
        tool_input: Optional[Dict[str, Any]] = None,
    ) -> ClassifiedError:
        """专门用于工具执行错误的分类"""
        classified = cls.classify(error, {"tool_name": tool_name, "input": tool_input})
        # 工具错误增加更具体的 fix_hint
        if classified.error_type == HydroErrorType.data_format:
            classified.recovery.fix_hint = (
                f"工具 {tool_name} 数据格式处理失败。"
                f"请检查输入数据是否符合要求，或先调用数据预览工具确认格式。"
            )
        elif classified.error_type == HydroErrorType.model_param:
            classified.recovery.fix_hint = (
                f"工具 {tool_name} 参数校验失败。"
                f"请参考工具描述中的参数说明和有效范围。"
            )
        return classified

    @classmethod
    def classify_model_error(
        cls,
        error: Exception,
        model_key: Optional[str] = None,
    ) -> ClassifiedError:
        """专门用于模型调用错误的分类"""
        classified = cls.classify(error, {"model_key": model_key})
        # 模型错误增加 fallback 建议
        if classified.error_type in (HydroErrorType.api_timeout, HydroErrorType.api_rate_limit):
            classified.recovery.fallback_model = cls._suggest_fallback(model_key)
        elif classified.error_type == HydroErrorType.api_billing:
            classified.recovery.fallback_model = cls._suggest_fallback(model_key)
        return classified

    @staticmethod
    def _suggest_fallback(model_key: Optional[str]) -> str:
        """根据当前配置动态推荐降级方案。"""
        try:
            from floodmind.agent.native.model_router import _build_fallback_chain
            chain = _build_fallback_chain()
            if model_key and model_key in chain and chain[model_key]:
                return chain[model_key][0]
            # 返回配置列表中第一个可用模型
            from floodmind.config.model_presets import get_models_list
            models = get_models_list()
            if models:
                return models[0]["key"]
        except Exception:
            pass
        return ""


def execute_with_recovery(
    callable_fn,
    *args,
    classifier: Optional[ErrorClassifier] = None,
    on_retry: Optional[callable] = None,
    on_fallback: Optional[callable] = None,
    on_abort: Optional[callable] = None,
    **kwargs,
):
    """
    通用带恢复的执行包装器。

    示例：
        result = execute_with_recovery(
            model_client.stream_chat,
            messages=messages,
            tools=tools,
            on_fallback=lambda c: switch_model(c.recovery.fallback_model),
        )
    """
    classifier = classifier or ErrorClassifier()
    last_error = None

    try:
        return callable_fn(*args, **kwargs)
    except Exception as e:
        classified = classifier.classify(e)
        recovery = classified.recovery

        if recovery.action == "abort":
            if on_abort:
                on_abort(classified)
            raise

        if recovery.action == "fallback" and recovery.fallback_model:
            if on_fallback:
                on_fallback(classified)
            # 尝试 fallback
            kwargs["model_key"] = recovery.fallback_model
            return callable_fn(*args, **kwargs)

        if recovery.action in ("retry", "retry_with_fix"):
            for attempt in range(recovery.max_retries):
                if recovery.backoff_seconds > 0:
                    import time
                    time.sleep(recovery.backoff_seconds * (2 ** attempt))
                if on_retry:
                    on_retry(classified, attempt)
                try:
                    return callable_fn(*args, **kwargs)
                except Exception as retry_e:
                    last_error = retry_e
                    continue

        # 重试/恢复都失败，抛出原始错误
        raise last_error or e
