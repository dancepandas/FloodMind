"""
Model Router — 模型管理增强

- 故障降级（fallback chain）
- 智能超时（按任务类型动态超时）
- Token 用量追踪（数据收集，不展示 UI）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Fallback Chain ────────────────────────────────────────────────
# 动态构建：从 settings.json 的 provider.models 读取可用模型列表，
# 按顺序选择下一个作为降级目标。不再硬编码具体模型名称。
# 注意：此模块加载时 config 尚未初始化，fallback chain 由 ModelRouter.__init__ 动态构建。

def _build_fallback_chain() -> Dict[str, List[str]]:
    """从当前配置动态构建降级链。"""
    try:
        from floodmind.config.model_presets import get_models_list
        models = get_models_list()
        keys = [m["key"] for m in models]
        chain: Dict[str, List[str]] = {}
        for i, key in enumerate(keys):
            candidates = keys[i + 1:i + 4]  # 后面最多 3 个作为降级候选
            if candidates:
                chain[key] = candidates
        return chain
    except Exception:
        return {}

# ── 智能超时配置 ──────────────────────────────────────────────────
# 按工具名/任务类型分配不同超时
SMART_TIMEOUTS: Dict[str, float] = {
    "default": 90.0,
    "preview_data": 15.0,
    "read_csv": 15.0,
    "read_file": 10.0,
    "list_files": 10.0,
    "run_hydro_model": 300.0,
    "run_chronos_forecast": 300.0,
    "run_tslm_forecast": 300.0,
    "generate_report": 120.0,
    "SubAgent": 180.0,
    "ParallelSubAgent": 300.0,
    "ParallelTask": 300.0,
}


@dataclass
class ModelCallConfig:
    """模型调用配置"""

    model_key: str
    timeout: float = 90.0
    max_retries: int = 2
    retry_backoff: float = 2.0
    enable_fallback: bool = True

    @classmethod
    def for_tool(cls, model_key: str, tool_name: str) -> "ModelCallConfig":
        """根据工具名创建合适的调用配置"""
        timeout = SMART_TIMEOUTS.get(tool_name, SMART_TIMEOUTS["default"])
        return cls(model_key=model_key, timeout=timeout)


@dataclass
class TokenUsageRecord:
    """单次调用的 Token 用量记录"""

    timestamp: float
    model_key: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0


class TokenUsageTracker:
    """
    Token 用量追踪器。
    纯数据收集，不做 UI 展示。数据通过 API 接口对外提供。
    """

    def __init__(self):
        self._records: List[TokenUsageRecord] = []

    def record(self, record: TokenUsageRecord) -> None:
        self._records.append(record)

    def get_session_summary(self) -> Dict[str, Any]:
        """获取当前会话汇总"""
        if not self._records:
            return {"total_calls": 0, "total_tokens": 0}

        by_model: Dict[str, Dict[str, int]] = {}
        total = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "total": 0}

        for r in self._records:
            total["input"] += r.input_tokens
            total["output"] += r.output_tokens
            total["reasoning"] += r.reasoning_tokens
            total["cache_read"] += r.cache_read_tokens
            total["cache_write"] += r.cache_write_tokens
            total["total"] += r.total_tokens

            if r.model_key not in by_model:
                by_model[r.model_key] = {"calls": 0, "tokens": 0}
            by_model[r.model_key]["calls"] += 1
            by_model[r.model_key]["tokens"] += r.total_tokens

        return {
            "total_calls": len(self._records),
            "total_tokens": total["total"],
            "input_tokens": total["input"],
            "output_tokens": total["output"],
            "reasoning_tokens": total["reasoning"],
            "cache_read_tokens": total["cache_read"],
            "cache_write_tokens": total["cache_write"],
            "by_model": by_model,
        }

    def get_records(self) -> List[TokenUsageRecord]:
        return list(self._records)

    def reset(self) -> None:
        self._records.clear()


class ModelRouter:
    """
    模型路由：故障降级 + 重试策略。
    本身不直接调用模型，只提供决策逻辑。
    """

    def __init__(
        self,
        fallback_chain: Optional[Dict[str, List[str]]] = None,
        tracker: Optional[TokenUsageTracker] = None,
    ):
        self.fallback_chain = fallback_chain or _build_fallback_chain()
        self.tracker = tracker or TokenUsageTracker()

    def get_fallback(self, model_key: str) -> Optional[str]:
        """获取降级模型"""
        candidates = self.fallback_chain.get(model_key, [])
        return candidates[0] if candidates else None

    def get_timeout_for_tool(self, tool_name: Optional[str]) -> float:
        """获取工具对应的超时时间"""
        if not tool_name:
            return SMART_TIMEOUTS["default"]
        return SMART_TIMEOUTS.get(tool_name, SMART_TIMEOUTS["default"])

    def record_usage_from_event(self, model_key: str, usage_event: Dict[str, Any]) -> None:
        """从 ModelEvent usage 中记录用量"""
        try:
            record = TokenUsageRecord(
                timestamp=time.time(),
                model_key=model_key,
                input_tokens=usage_event.get("prompt_tokens", 0),
                output_tokens=usage_event.get("completion_tokens", 0),
                reasoning_tokens=usage_event.get("reasoning_tokens", 0),
                cache_read_tokens=usage_event.get("cache_read_tokens", 0),
                cache_write_tokens=usage_event.get("cache_write_tokens", 0),
                total_tokens=usage_event.get("total_tokens", 0),
            )
            self.tracker.record(record)
        except Exception as e:
            logger.debug("Token usage recording skipped: %s", e)
