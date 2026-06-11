"""
Tool Call Guardrails — 纯状态机，无副作用

借鉴 Hermes agent/tool_guardrails.py，针对 FloodMind 水文场景定制。
检测三类工具调用恶性循环：
  1. 精确重复：相同工具 + 相同参数连续失败
  2. 失败螺旋：同一工具反复失败（参数不同）
  3. 无进展幂等：幂等工具反复调用但结果无变化

设计原则：
  - 默认仅 warning，不阻断执行
  - hard_stop 需显式配置开启
  - 所有决策通过 event_bus 透出，前端可展示
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set

# ── 水文场景幂等工具清单 ──────────────────────────────────────────
# 读取类/查询类工具：多次调用结果应一致，反复调用无意义
IDEMPOTENT_TOOL_NAMES: Set[str] = {
    "preview_data",
    "read_csv",
    "read_file",
    "list_files",
    "get_file_info",
    "check_model_status",
    "search_knowledge",
    "get_weather_data",
    "get_hydro_data",
}

# 可变工具：会改变状态，即使参数相同也可能因状态变化而有不同结果
MUTATING_TOOL_NAMES: Set[str] = {
    "run_hydro_model",
    "run_chronos_forecast",
    "run_tslm_forecast",
    "write_file",
    "generate_report",
    "create_plan",
    "SubAgent",
    "ParallelSubAgent",
    "ParallelTask",
}


@dataclass(frozen=True)
class ToolCallSignature:
    """工具调用签名：工具名 + 规范化参数哈希"""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Optional[Mapping[str, Any]]) -> "ToolCallSignature":
        canonical = _canonical_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> Dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash[:16]}


@dataclass(frozen=True)
class GuardrailDecision:
    """护栏决策结果"""

    action: str = "allow"  # allow | warn | block
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: Optional[ToolCallSignature] = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_block(self) -> bool:
        return self.action == "block"

    def to_metadata(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


@dataclass
class GuardrailConfig:
    """护栏配置阈值"""

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    idempotent_tools: Set[str] = field(default_factory=lambda: set(IDEMPOTENT_TOOL_NAMES))


class ToolGuardrail:
    """
    工具调用护栏控制器。

    使用方式：
        guardrail = ToolGuardrail()
        # 每次工具调用后
        decision = guardrail.check(tool_name, args, result)
        if decision.should_block:
            # 阻断执行，生成 synthetic error 给 LLM
        elif decision.action == "warn":
            # 记录警告，前端可展示，但允许继续
    """

    def __init__(self, config: Optional[GuardrailConfig] = None):
        self.config = config or GuardrailConfig()
        # 精确调用历史：signature -> 最近结果列表
        self._exact_history: Dict[ToolCallSignature, List[Dict[str, Any]]] = {}
        # 同工具失败历史：tool_name -> 连续失败次数
        self._same_tool_failures: Dict[str, int] = {}
        # 幂等工具无进展历史：signature -> 上次结果哈希
        self._idempotent_last_result: Dict[ToolCallSignature, str] = {}

    def check(
        self,
        tool_name: str,
        args: Optional[Mapping[str, Any]],
        result: Any,
    ) -> GuardrailDecision:
        """检查单次工具调用，返回决策。"""
        signature = ToolCallSignature.from_call(tool_name, args)
        is_error = self._is_error_result(result)
        result_hash = self._result_hash(result)

        # 1. 精确重复检测
        decision = self._check_exact_failure(signature, is_error)
        if decision.action != "allow":
            return decision

        # 2. 失败螺旋检测
        decision = self._check_same_tool_failure(tool_name, is_error)
        if decision.action != "allow":
            return decision

        # 3. 幂等工具无进展检测
        decision = self._check_no_progress(signature, tool_name, result_hash, is_error)
        if decision.action != "allow":
            return decision

        return GuardrailDecision()

    def _check_exact_failure(
        self,
        signature: ToolCallSignature,
        is_error: bool,
    ) -> GuardrailDecision:
        """检测相同工具 + 相同参数是否连续失败。"""
        history = self._exact_history.setdefault(signature, [])
        history.append({"error": is_error, "timestamp": __import__("time").time()})
        # 只保留最近 20 次
        if len(history) > 20:
            history = history[-20:]
        self._exact_history[signature] = history

        # 统计最近连续失败次数
        consecutive = 0
        for h in reversed(history):
            if h["error"]:
                consecutive += 1
            else:
                break

        if consecutive >= self.config.exact_failure_block_after and self.config.hard_stop_enabled:
            return GuardrailDecision(
                action="block",
                code="exact_failure_block",
                message=(
                    f"工具 {signature.tool_name} 以相同参数连续失败 {consecutive} 次，"
                    f"已达到阻断阈值。请检查参数是否正确，或换一种方式完成任务。"
                ),
                tool_name=signature.tool_name,
                count=consecutive,
                signature=signature,
            )

        if consecutive >= self.config.exact_failure_warn_after and self.config.warnings_enabled:
            return GuardrailDecision(
                action="warn",
                code="exact_failure_warn",
                message=(
                    f"工具 {signature.tool_name} 以相同参数连续失败 {consecutive} 次，"
                    f"请检查参数是否正确。"
                ),
                tool_name=signature.tool_name,
                count=consecutive,
                signature=signature,
            )

        return GuardrailDecision()

    def _check_same_tool_failure(
        self,
        tool_name: str,
        is_error: bool,
    ) -> GuardrailDecision:
        """检测同一工具（不同参数）是否反复失败。"""
        if is_error:
            self._same_tool_failures[tool_name] = self._same_tool_failures.get(tool_name, 0) + 1
        else:
            self._same_tool_failures[tool_name] = 0

        count = self._same_tool_failures.get(tool_name, 0)

        if count >= self.config.same_tool_failure_halt_after and self.config.hard_stop_enabled:
            return GuardrailDecision(
                action="block",
                code="same_tool_failure_block",
                message=(
                    f"工具 {tool_name} 已连续失败 {count} 次（不同参数），"
                    f"强制终止以避免资源浪费。请换一种工具或调整任务目标。"
                ),
                tool_name=tool_name,
                count=count,
            )

        if count >= self.config.same_tool_failure_warn_after and self.config.warnings_enabled:
            return GuardrailDecision(
                action="warn",
                code="same_tool_failure_warn",
                message=(
                    f"工具 {tool_name} 已连续失败 {count} 次（不同参数），"
                    f"建议换一种方法。"
                ),
                tool_name=tool_name,
                count=count,
            )

        return GuardrailDecision()

    def _check_no_progress(
        self,
        signature: ToolCallSignature,
        tool_name: str,
        result_hash: str,
        is_error: bool,
    ) -> GuardrailDecision:
        """检测幂等工具是否反复调用但结果无变化。"""
        if tool_name not in self.config.idempotent_tools:
            return GuardrailDecision()

        if is_error:
            # 出错时不检测无进展
            return GuardrailDecision()

        last_hash = self._idempotent_last_result.get(signature)
        if last_hash is not None and last_hash == result_hash:
            # 结果与上次完全相同，记录无进展
            # 通过 _exact_history 的长度来估算无进展次数
            history = self._exact_history.get(signature, [])
            no_progress_count = sum(
                1 for h in history[-10:]
                if not h["error"] and h.get("no_progress", False)
            ) + 1

            # 标记最近一条为 no_progress
            if history:
                history[-1]["no_progress"] = True

            if no_progress_count >= self.config.no_progress_block_after and self.config.hard_stop_enabled:
                return GuardrailDecision(
                    action="block",
                    code="no_progress_block",
                    message=(
                        f"幂等工具 {tool_name} 已连续 {no_progress_count} 次返回相同结果，"
                        f"无继续调用必要。请基于已有结果进行下一步分析。"
                    ),
                    tool_name=tool_name,
                    count=no_progress_count,
                    signature=signature,
                )

            if no_progress_count >= self.config.no_progress_warn_after and self.config.warnings_enabled:
                return GuardrailDecision(
                    action="warn",
                    code="no_progress_warn",
                    message=(
                        f"幂等工具 {tool_name} 已连续 {no_progress_count} 次返回相同结果，"
                        f"建议停止重复查询，继续下一步。"
                    ),
                    tool_name=tool_name,
                    count=no_progress_count,
                    signature=signature,
                )

        # 更新上次结果哈希
        self._idempotent_last_result[signature] = result_hash
        return GuardrailDecision()

    @staticmethod
    def _is_error_result(result: Any) -> bool:
        """判断工具结果是否为错误。"""
        if result is None:
            return True
        # 支持 ToolResult
        if hasattr(result, "status"):
            return result.status in ("error", "failed")
        # 支持字典
        if isinstance(result, dict):
            return result.get("status") in ("error", "failed") or result.get("error") is True
        # 字符串结果：包含"错误"或"Error"前缀
        if isinstance(result, str):
            return result.strip().startswith(("错误", "Error", "ERROR", "Exception"))
        return False

    @staticmethod
    def _result_hash(result: Any) -> str:
        """对工具结果取哈希，用于无进展检测。"""
        try:
            if hasattr(result, "content"):
                data = result.content
            elif hasattr(result, "to_dict"):
                data = result.to_dict()
            else:
                data = result
            canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
            return _sha256(canonical)
        except Exception:
            return ""

    def reset(self) -> None:
        """重置所有状态（如会话切换时）。"""
        self._exact_history.clear()
        self._same_tool_failures.clear()
        self._idempotent_last_result.clear()


def _canonical_args(args: Mapping[str, Any]) -> str:
    """返回排序后的紧凑 JSON，用于参数哈希。"""
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
