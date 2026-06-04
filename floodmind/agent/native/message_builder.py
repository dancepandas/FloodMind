"""
Native Agent Runtime - MessageBuilder

构造 OpenAI 兼容消息格式，支持多模态 image_url content parts。
"""

import base64
import json
import logging
from typing import List, Optional

from floodmind.agent.native.types import Attachment

logger = logging.getLogger(__name__)


def build_data_url(path: str, mime_type: str) -> str:
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


class MessageBuilder:
    def build_system_message(self, prompt: str) -> dict:
        return {"role": "system", "content": prompt}

    def build_user_message(self, text: str, attachments: Optional[List[Attachment]] = None) -> dict:
        attachments = attachments or []
        image_attachments = [a for a in attachments if a.kind == "image"]
        if not image_attachments:
            return {"role": "user", "content": text}

        content: List[dict] = [{"type": "text", "text": text}]
        for image in image_attachments:
            try:
                data_url = build_data_url(image.path, image.mime_type)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
                logger.info(
                    "MessageBuilder: attached image %s (%s, %d bytes)",
                    image.name,
                    image.mime_type,
                    image.size,
                )
            except Exception as e:
                logger.warning("MessageBuilder: failed to build data URL for %s: %s", image.name, e)
        return {"role": "user", "content": content}

    @staticmethod
    def build_assistant_tool_calls_message(tool_calls: list, text_content: str = "") -> dict:
        openai_tool_calls = []
        for tc in tool_calls:
            openai_tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments, ensure_ascii=False),
                },
            })
        msg: dict = {"role": "assistant"}
        if text_content:
            msg["content"] = text_content
        if openai_tool_calls:
            msg["tool_calls"] = openai_tool_calls
        if "content" not in msg and "tool_calls" not in msg:
            msg["content"] = ""
        return msg

    @staticmethod
    def build_tool_result_message(tool_call_id: str, content: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    def build_memory_messages(self, memory_messages: list) -> List[dict]:
        result = []
        for msg in memory_messages:
            role = getattr(msg, "type", None) or getattr(msg, "role", None)
            if role == "human":
                api_role = "user"
            elif role == "ai":
                api_role = "assistant"
            elif role == "system":
                api_role = "system"
            else:
                continue

            content = getattr(msg, "content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts)

            additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}

            if api_role == "user":
                result.append({"role": "user", "content": str(content)})
            elif api_role == "assistant":
                parts = [f"FloodMind:\n最终回答: {str(content)}"]
                reasoning = additional_kwargs.get("reasoning", "")
                if reasoning:
                    reasoning_summary = reasoning if len(reasoning) <= 300 else reasoning[:300] + "…"
                    parts.append(f"思考摘要: {reasoning_summary}")
                tool_calls = additional_kwargs.get("tool_calls", [])
                if tool_calls:
                    tc_lines = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            name = str(tc.get("tool_name", ""))
                            output = str(tc.get("tool_output", ""))
                            output_summary = output if len(output) <= 150 else output[:150] + "…"
                            tc_lines.append(f"- 工具: {name}, 输出摘要: {output_summary}")
                    if tc_lines:
                        parts.append("工具调用:\n" + "\n".join(tc_lines))
                result.append({"role": "assistant", "content": "\n".join(parts)})
            elif api_role == "system":
                result.append({"role": "system", "content": str(content)})

        return result
