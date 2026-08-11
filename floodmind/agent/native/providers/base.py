"""ProviderCodec 抽象基类 —— 厂商专属编解码/调用管线的统一接口。

每厂商一个 codec（由原 ProviderPipeline 收敛重命名而来），负责三层「线上方言」翻译
（不碰编排/记忆）：

1. ``prepare_request``   —— 请求参数适配（思考开关方言 / max_tokens 命名 / 禁传参数剥离）
2. ``prepare_messages``  —— 消息适配（思维链回传策略 + 多模态 block 归一化/校验）
3. 流式解析钩子          —— ``extract_reasoning`` / ``filter_content`` / ``extract_usage``

加上 ``codec.ProviderCodec`` 提供的纯编解码面（§7.3）：``encode_request`` /
``decode_chunk``（Provider Raw Event → Canonical Parts）/ ``decode_message``。

解析侧每次流式调用持有独立 ``StreamState``（``new_stream_state()``）。
ModelClient 构造时经 ``route_codec()`` 绑定一条 codec，之后不再感知厂商差异。

基类默认实现 = 标准 OpenAI 行为（兜底）：

- ``reasoning_content`` → ``reasoning`` 字段提取思考，兼容累积式全量帧
- usage 取 chunk 顶层 ``usage``（stream_options.include_usage 的末帧空 choices chunk）
- 流式请求补 ``stream_options.include_usage``
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .codec import ProviderCodec as _CodecBase

# ---------------------------------------------------------------------------
# 共享工具
# ---------------------------------------------------------------------------

_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"


def incremental(buffer: str, new_text: str) -> Tuple[str, str]:
    """把累积式/增量式文本统一为增量。返回 (新 buffer, 本次增量)。

    某些厂商（如 MiniMax reasoning_details）每帧发的是全量文本：
    若 new_text 以 buffer 开头则视为累积式，只取差分；否则按增量式追加。
    """
    if buffer and new_text.startswith(buffer):
        return new_text, new_text[len(buffer):]
    return buffer + new_text, new_text


def _partial_tag_suffix(s: str, tag: str) -> str:
    """s 末尾与 tag 前缀匹配的最长片段（流式跨 chunk 的不完整标签）。"""
    for n in range(min(len(tag) - 1, len(s)), 0, -1):
        if s.endswith(tag[:n]):
            return tag[:n]
    return ""


def split_think_tags(raw: str) -> Tuple[str, str]:
    """把含 <think>...</think> 的（可能未闭合的）文本拆成 (answer, reasoning)。

    流式安全：末尾不完整的标签片段（如 ``<thi``）暂扣不计入任何一侧，
    等后续 chunk 补齐后自然归入正确一侧——保证两侧的已发长度单调不减。
    """
    answer_parts: List[str] = []
    reasoning_parts: List[str] = []
    rest = raw
    while rest:
        start = rest.find(_OPEN_TAG)
        if start == -1:
            tail = _partial_tag_suffix(rest, _OPEN_TAG)
            answer_parts.append(rest[: len(rest) - len(tail)])
            break
        answer_parts.append(rest[:start])
        rest = rest[start + len(_OPEN_TAG):]
        end = rest.find(_CLOSE_TAG)
        if end == -1:
            tail = _partial_tag_suffix(rest, _CLOSE_TAG)
            reasoning_parts.append(rest[: len(rest) - len(tail)])
            rest = ""
        else:
            reasoning_parts.append(rest[:end])
            rest = rest[end + len(_CLOSE_TAG):]
    return "".join(answer_parts), "".join(reasoning_parts)


def usage_to_dict(usage: Any) -> Optional[Dict[str, int]]:
    """usage 对象/dict → 统一三项 dict；取不到返回 None。"""
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "total_tokens": usage.get("total_tokens") or 0,
    }


# ---------------------------------------------------------------------------
# 流式解析状态
# ---------------------------------------------------------------------------

@dataclass
class StreamState:
    """单次流式调用的解析状态（codec 私有，ModelClient 不解读字段）。"""

    reasoning_buffer: str = ""       # 累积式 reasoning 去重
    content_raw: str = ""            # <think> 解析用的 content 累积（兼容累积式方言）
    answer_emitted: int = 0          # 已发出的 answer 长度（split_think_tags 差分用）
    reasoning_tag_emitted: int = 0   # 已发出的标签思考长度
    usage_emitted: bool = False      # usage 事件每次流只发一次


# ---------------------------------------------------------------------------
# Codec 基类（默认 = OpenAI 标准行为）
# ---------------------------------------------------------------------------

class ProviderCodec(_CodecBase):
    """厂商 Codec 基类。默认实现即 OpenAI 兼容兜底行为。

    继承自 ``codec.ProviderCodec``（纯编解码面：``encode_request`` / ``decode_chunk`` /
    ``decode_message``），并叠加厂商调用管线方言（``prepare_request`` / ``prepare_messages`` /
    流式解析钩子）。``name`` 以类属性为准（子类声明），允许实例化时显式覆盖。

    ``conservative``：仅模型名前缀命中路由时置 True（如聚合网关托管的
    ``MiniMax/xxx``）——解析适配全部启用，请求适配退化为标准行为，
    避免网关不认厂商方言参数而报错。
    """

    name: str = "base"
    conservative: bool = False
    uses_max_completion_tokens: bool = False
    provider_id: str = ""
    model_id: str = ""
    base_url: str = ""

    def __init__(self, name: Optional[str] = None):
        """保留类级 ``name``（如 ``DashScopeCodec.name="dashscope"``），并允许显式覆盖。"""
        self.name = name or self.name

    # ── 路由 ────────────────────────────────────────────────────────

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        """匹配打分：0 = 不匹配。base_url 精确(100) > provider id(60) > 模型名前缀(40)。"""
        return 0

    # ── 请求侧 ──────────────────────────────────────────────────────

    def prepare_request(
        self,
        params: Dict[str, Any],
        *,
        enable_thinking: bool,
        stream: bool,
    ) -> Dict[str, Any]:
        """发送前翻译请求参数。默认只补 stream_options.include_usage。

        调用方显式给的 extra_body 已在 params 里——子类一律用 setdefault
        注入厂商参数，保证显式传参优先级最高。
        """
        if stream:
            params.setdefault("stream_options", {"include_usage": True})
        if not self.conservative and self.uses_max_completion_tokens and "max_tokens" in params:
            params["max_completion_tokens"] = params.pop("max_tokens")
        return params

    def prepare_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """消息适配（思维链回传策略 + 多模态 block 校验）。默认原样通过。"""
        return messages

    # ── assistant message 回传快照（流式）────────────────────────────

    def capture_assistant_delta(
        self,
        delta: Any,
        state: "StreamState",
        accumulator: Dict[str, Any],
    ) -> None:
        """收集 provider 原生 assistant delta，用于构造后续 API 回传消息。

        这条路径服务于 provider 协议对齐：UI 可展示过滤后的 answer/reasoning，
        但发给下一轮 API 的 assistant message 应尽量保留模型原始返回字段。
        默认 OpenAI 方言只保留 role/content/reasoning_content。
        """
        role = getattr(delta, "role", None)
        if role:
            accumulator["role"] = str(role)

        content = getattr(delta, "content", None)
        if content:
            accumulator["content"] = accumulator.get("content", "") + str(content)

        reasoning_content = getattr(delta, "reasoning_content", None)
        if reasoning_content:
            # extract_reasoning 已经维护了累积式去重 buffer；优先用 buffer 的全量。
            accumulator["reasoning_content"] = state.reasoning_buffer or str(reasoning_content)

    def build_assistant_message(
        self,
        accumulator: Dict[str, Any],
        tool_call_accumulators: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """把流式累积结果转为可回传给 provider 的 assistant message。"""
        msg: Dict[str, Any] = {"role": accumulator.get("role") or "assistant"}
        # MiniMax 等 schema 将 assistant.content 标为 required；工具调用轮即使为空也保留。
        msg["content"] = accumulator.get("content", "")
        if accumulator.get("reasoning_content"):
            msg["reasoning_content"] = accumulator["reasoning_content"]

        if tool_call_accumulators:
            msg["tool_calls"] = [
                {
                    "id": acc.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": acc.get("name") or "",
                        "arguments": acc.get("arguments") or "{}",
                    },
                }
                for acc in tool_call_accumulators
            ]
        return msg

    # ── 解析侧（流式）───────────────────────────────────────────────

    def new_stream_state(self) -> StreamState:
        return StreamState()

    def extract_reasoning(self, delta: Any, state: StreamState) -> Optional[str]:
        """从流式 delta 提取思考增量。标准方言：reasoning_content → reasoning。"""
        text = getattr(delta, "reasoning_content", None)
        if text:
            state.reasoning_buffer, inc = incremental(state.reasoning_buffer, str(text))
            return inc or None
        text = getattr(delta, "reasoning", None)
        if text:
            return str(text)
        return None

    def filter_content(self, text: str, state: StreamState) -> Tuple[str, str]:
        """剥离 content 中的思考标签，返回 (answer 增量, reasoning 增量)。

        默认方言 content 不含标签，原样放行。
        """
        return text, ""

    def extract_usage(self, chunk: Any) -> Optional[Dict[str, int]]:
        """从流式 chunk 提取 usage。标准位置：chunk 顶层 usage。"""
        return usage_to_dict(getattr(chunk, "usage", None))

    # ── 解析侧（非流式）─────────────────────────────────────────────

    def extract_message_reasoning(self, message: Any) -> Optional[str]:
        """非流式响应的思考字段：reasoning_content → reasoning。"""
        return (
            getattr(message, "reasoning_content", None)
            or getattr(message, "reasoning", None)
        )

    def extract_response_usage(self, response: Any) -> Optional[Dict[str, int]]:
        """非流式响应的 usage（顶层）。"""
        return usage_to_dict(getattr(response, "usage", None))
