"""MiniMax codec。

方言要点（docs/minimax.txt）：
- 思考开关：``thinking: {"type": "adaptive"/"disabled"}``（M3 可关；M2.x 强制思考，不可发 disabled）
- ``reasoning_split: true``：思考拆到 reasoning_content/reasoning_details（开启思考的默认体验）
  否则思考混在 content 的 <think>...</think> 标签里 → filter_content 流式剥离
- reasoning_details 数组的 text 为累积式全量 → incremental() 转增量
- max_tokens 弃用 → max_completion_tokens；temperature 范围 [0,2] 钳制
- usage 宽容解析（schema 仅保证 total_tokens）
- 多模态：image_url/video_url 原样放行（detail/fps/mm_file:// 透传）
"""

from typing import Any, Dict, List, Tuple

from .base import ProviderCodec, StreamState, incremental, split_think_tags


class MiniMaxCodec(ProviderCodec):
    name = "minimax"
    uses_max_completion_tokens = True

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        if "minimax" in (base_url or "").lower():
            return 100
        if (provider_id or "").lower() == "minimax":
            return 60
        if "minimax" in (model_id or "").lower():
            return 40
        return 0

    def prepare_request(
        self,
        params: Dict[str, Any],
        *,
        enable_thinking: bool,
        stream: bool,
    ) -> Dict[str, Any]:
        params = super().prepare_request(params, enable_thinking=enable_thinking, stream=stream)
        if self.conservative:
            return params

        model = str(params.get("model", "")).lower()
        is_m3 = "m3" in model

        # temperature 范围 [0,2]，超范围报错 → 钳制
        temp = params.get("temperature")
        if isinstance(temp, (int, float)):
            params["temperature"] = max(0.0, min(2.0, float(temp)))

        extra = dict(params.get("extra_body") or {})
        if enable_thinking:
            extra.setdefault("thinking", {"type": "adaptive"})
            extra.setdefault("reasoning_split", True)
        elif is_m3:
            extra.setdefault("thinking", {"type": "disabled"})
        # M2.x 强制思考：关闭请求只省略参数（不可发 disabled）
        if extra:
            params["extra_body"] = extra
        return params

    def capture_assistant_delta(
        self,
        delta: Any,
        state: StreamState,
        accumulator: Dict[str, Any],
    ) -> None:
        """保留 MiniMax 原生 assistant message 字段，供多轮工具调用回传。

        MiniMax 文档要求 Function Call 历史中完整保留 response_message：
        - reasoning_split=True 时保留 reasoning_content / reasoning_details；
        - reasoning_split=False 时 content 内 <think>...</think> 也必须原样保留。
        """
        super().capture_assistant_delta(delta, state, accumulator)

        details = getattr(delta, "reasoning_details", None)
        if details:
            normalized: List[Dict[str, Any]] = []
            for detail in details:
                if isinstance(detail, dict):
                    normalized.append(dict(detail))
                elif hasattr(detail, "model_dump"):
                    normalized.append(detail.model_dump())
                else:
                    item: Dict[str, Any] = {}
                    for key in ("type", "id", "format", "index", "text"):
                        value = getattr(detail, key, None)
                        if value is not None:
                            item[key] = value
                    if item:
                        normalized.append(item)
            if normalized:
                # MiniMax 流式 reasoning_details 的 text 是累积式全量；回传时保留最新完整结构。
                accumulator["reasoning_details"] = normalized
                if state.reasoning_buffer:
                    accumulator["reasoning_content"] = state.reasoning_buffer

    def build_assistant_message(
        self,
        accumulator: Dict[str, Any],
        tool_call_accumulators: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        msg = super().build_assistant_message(accumulator, tool_call_accumulators)
        if accumulator.get("reasoning_details"):
            msg["reasoning_details"] = accumulator["reasoning_details"]
        return msg

    def extract_reasoning(self, delta: Any, state: StreamState):
        """reasoning_content 优先；reasoning_details 的 text 为累积式 → 差分。"""
        text = getattr(delta, "reasoning_content", None)
        if text:
            state.reasoning_buffer, inc = incremental(state.reasoning_buffer, str(text))
            return inc or None

        details = getattr(delta, "reasoning_details", None)
        if details:
            full = "".join(
                str(d.get("text", "") if isinstance(d, dict) else getattr(d, "text", ""))
                for d in details
            )
            if full:
                state.reasoning_buffer, inc = incremental(state.reasoning_buffer, full)
                return inc or None

        return super().extract_reasoning(delta, state)

    def filter_content(self, text: str, state: StreamState) -> Tuple[str, str]:
        """<think> 标签流式剥离（reasoning_split 关闭时 M2.x 的原生格式）。

        content 帧同样兼容累积式方言；split_think_tags 保证两侧已发长度单调不减。
        """
        state.content_raw, _ = incremental(state.content_raw, text)
        answer, reasoning = split_think_tags(state.content_raw)
        answer_inc = answer[state.answer_emitted:]
        reasoning_inc = reasoning[state.reasoning_tag_emitted:]
        state.answer_emitted = len(answer)
        state.reasoning_tag_emitted = len(reasoning)
        return answer_inc, reasoning_inc
