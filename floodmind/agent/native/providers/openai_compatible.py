"""OpenAI 兼容兜底 pipeline —— 标准 Chat Completions 行为。

覆盖所有没有专属 pipeline 的 provider（OpenAI 官方 / Ollama / Groq / 各类网关）。
行为 = ProviderPipeline 基类默认；``prepare_messages`` 原样放行。
"""

from typing import Any, Dict, List

from .base import ProviderPipeline


class OpenAICompatiblePipeline(ProviderPipeline):
    """标准 OpenAI 方言：reasoning_content、顶层 usage、无厂商参数。"""

    name = "openai-compatible"

    def prepare_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return messages
