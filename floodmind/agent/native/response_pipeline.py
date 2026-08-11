"""ResponsePipeline（目标 §7.4）：Provider-neutral 状态化组装。

把 ``ModelClient.stream_chat`` 里内联的 tool delta 累积 / 终态判定 / usage 累积
抽成独立组件：消费 ``CanonicalPart``（ProviderCodec.decode_chunk 产出），
不感知任何 provider 原生块。语义：

- indexed tool delta 跨 chunk 累积（按 index 归并，名称后到覆盖前到）
- malformed JSON → ``InvalidToolCall``（保留 raw arguments，绝不转成可执行默认值）
- raw 参数保留；assistant 完整 replay snapshot；最终累积 usage
- 终态判定：refusal / content_filter / max_tokens 不误判 completed
"""

import json
from typing import Dict, List, Optional, Tuple

from floodmind.agent.native.types import InvalidToolCall, TerminalReason, ToolCall
from floodmind.agent.runtime.contracts.canonical_parts import CanonicalPart


class ResponsePipeline:
    def __init__(self):
        self._tool_acc: Dict[int, Dict[str, str]] = {}
        self._completed: List[Dict[str, str]] = []
        self._assistant: Dict[str, object] = {"role": "assistant", "content": ""}
        self._reasoning: List[str] = []
        self._usage: Dict[str, int] = {}
        self._terminal: Optional[str] = None
        self._raw_blocks: List[dict] = []

    def accumulate(self, part: CanonicalPart) -> None:
        if part.raw:
            self._raw_blocks.append(part.raw)
        if part.event == "text_delta":
            self._assistant["content"] = str(self._assistant.get("content", "")) + part.text
        elif part.event == "reasoning_delta":
            self._reasoning.append(part.text)
        elif part.event == "tool_call_delta":
            acc = self._tool_acc.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
            if part.id:
                acc["id"] = part.id
            if part.name:
                acc["name"] = part.name
            acc["arguments"] += part.arguments
        elif part.event == "usage":
            try:
                self._usage = json.loads(part.text)
            except (json.JSONDecodeError, TypeError):
                pass
        elif part.event == "response_end":
            self._terminal = part.text

    def _finalize_one(self, acc: Dict[str, str]) -> Tuple[Optional[ToolCall], Optional[InvalidToolCall]]:
        # 把解析出的 id 写回 accumulator，保证 ToolCall.id 与 assistant 消息
        # tool_calls[].id 一致（流式空 id 的 fallback 也由此对齐）。
        if not acc.get("id"):
            acc["id"] = f"call_{id(acc)}"
        arguments_str = acc.get("arguments", "")
        if not arguments_str:
            return ToolCall(id=acc["id"], name=acc.get("name", ""), arguments={}), None
        try:
            parsed = json.loads(arguments_str)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, InvalidToolCall(id=acc["id"], name=acc.get("name", ""),
                                         raw_arguments=arguments_str, error=f"工具参数不是有效 JSON: {exc}")
        if not isinstance(parsed, dict):
            return None, InvalidToolCall(id=acc["id"], name=acc.get("name", ""),
                                         raw_arguments=arguments_str, error="工具参数 JSON 必须是对象。")
        return ToolCall(id=acc["id"], name=acc.get("name", ""), arguments=parsed), None

    def finalize(self) -> Tuple[List[ToolCall], List[InvalidToolCall]]:
        """把当前累积的 tool deltas 收口为 ToolCall / InvalidToolCall。

        收口后的 accumulator（含 fallback id / raw arguments）进入 ``_completed``，
        供 assistant 消息回传快照使用；本轮累积清空。
        """
        calls, invalids = [], []
        for idx, acc in sorted(self._tool_acc.items()):
            call, invalid = self._finalize_one(acc)
            if call is not None:
                calls.append(call)
            elif invalid is not None:
                invalids.append(invalid)
            self._completed.append(dict(acc))
        self._tool_acc = {}
        return calls, invalids

    def completed_accumulators(self) -> List[Dict[str, str]]:
        """已收口的 tool accumulator 快照（assistant 消息回传用）。"""
        return list(self._completed)

    def assistant_snapshot(self) -> dict:
        return dict(self._assistant)

    def cumulative_usage(self) -> dict:
        return dict(self._usage)

    def terminal_reason(self) -> TerminalReason:
        return TerminalReason.from_raw(self._terminal)

    def is_partial_attempt(self) -> bool:
        return bool(self._tool_acc) or not self._terminal

    def reset(self) -> None:
        self.__init__()
