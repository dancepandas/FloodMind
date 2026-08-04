"""DeepSeek 官方 pipeline。

方言要点（docs/deepseek.txt）：
- 思考开关：``extra_body={"thinking": {"type": "enabled"/"disabled"}}``，默认 enabled
- 思考模式下 temperature/top_p/penalties 不生效 → 发送前剥离，保持请求干净
- 思考内容：message/delta.reasoning_content（标准位置，基类默认处理）
- 多轮：有工具调用的轮次必须回传 reasoning_content 否则 400
  （prepare_messages 钩子保留——思维链持久化进 _turns 为后续项，现状不回归）
"""

from typing import Any, Dict

from .base import ProviderPipeline


class DeepSeekPipeline(ProviderPipeline):
    name = "deepseek"

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        if "deepseek" in (base_url or "").lower():
            return 100
        if (provider_id or "").lower() == "deepseek":
            return 60
        if (model_id or "").lower().startswith("deepseek"):
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

        extra = dict(params.get("extra_body") or {})
        extra.setdefault("thinking", {"type": "enabled" if enable_thinking else "disabled"})
        params["extra_body"] = extra

        if enable_thinking:
            # 思考模式下这些采样参数不生效（deepseek.txt L22）
            for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
                params.pop(key, None)
        return params
