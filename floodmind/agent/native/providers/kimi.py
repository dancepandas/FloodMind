"""Kimi（Moonshot）pipeline —— 按模型代际分支。

方言要点（docs/kimi.txt）：
- kimi-k3：始终思考，无开关（不应传 thinking）
- kimi-k2.7-code(-highspeed)：强制思考 + Preserved Thinking，thinking.type 仅允许 enabled
- kimi-k2.6：thinking.type 可开关；Agent 场景 thinking.keep="all" 跨轮保留思维链
- kimi-k2.5：可关思考，无 keep
- k2.6/k2.7-code 的 temperature 不可显式传入 → 剥离
  （实测 k2.5 同样仅允许 temperature=1，已知代际统一剥离）
- max_tokens 已弃用 → max_completion_tokens
- 流式 usage 嵌在末帧 choices[0].usage（非标位置），顶层 usage 作回退
- 多模态：不支持公网 URL 图片（仅 base64 / ms:// 引用）→ prepare_messages 早失败
"""

from typing import Any, Dict, List

from .base import ProviderPipeline, usage_to_dict


class KimiPipeline(ProviderPipeline):
    name = "kimi"

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        if "moonshot" in (base_url or "").lower():
            return 100
        if (provider_id or "").lower() in ("kimi", "moonshot"):
            return 60
        if (model_id or "").lower().startswith(("kimi", "moonshot")):
            return 40
        return 0

    @staticmethod
    def _generation(model: str) -> str:
        """模型代际：k3 / k2.7 / k2.6 / k2.5 / unknown。"""
        m = model.lower()
        if "kimi-k3" in m:
            return "k3"
        if "k2.7" in m:
            return "k2.7"
        if "k2.6" in m:
            return "k2.6"
        if "k2.5" in m:
            return "k2.5"
        return "unknown"

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

        # max_tokens 已弃用 → max_completion_tokens
        if "max_tokens" in params:
            params["max_completion_tokens"] = params.pop("max_tokens")

        gen = self._generation(str(params.get("model", "")))

        # k 系列 temperature 锁死为 1，显式传入直接 400（kimi.txt L129/L200/L251 + 实测 k2.5）
        if gen != "unknown":
            params.pop("temperature", None)

        extra = dict(params.get("extra_body") or {})
        if gen == "k3":
            pass  # 始终思考，不传 thinking
        elif gen == "k2.7":
            # 强制思考，仅允许 enabled；关闭请求只省略
            if enable_thinking:
                extra.setdefault("thinking", {"type": "enabled"})
        elif gen == "k2.6":
            thinking: Dict[str, Any] = {"type": "enabled" if enable_thinking else "disabled"}
            if enable_thinking:
                thinking["keep"] = "all"  # Agent 多轮工具调用保留思维链
            extra.setdefault("thinking", thinking)
        elif gen == "k2.5":
            extra.setdefault("thinking", {"type": "enabled" if enable_thinking else "disabled"})
        # unknown：不发 thinking，避免新模型拒绝未知参数
        if extra:
            params["extra_body"] = extra
        return params

    def extract_usage(self, chunk: Any):
        """Kimi 流式 usage 在末帧 choices[0].usage（非标），顶层 usage 作回退。"""
        choices = getattr(chunk, "choices", None) or []
        if choices:
            usage = usage_to_dict(getattr(choices[0], "usage", None))
            if usage:
                return usage
        return super().extract_usage(chunk)

    def prepare_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Kimi 不支持公网 URL 图片——尽早抛出清晰错误，避免请求打过去才 400。"""
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "image_url":
                    continue
                url = block.get("image_url")
                url = url.get("url", "") if isinstance(url, dict) else str(url)
                if url.startswith(("http://", "https://")):
                    raise ValueError(
                        "Kimi 不支持公网 URL 图片，请改用 base64 数据或文件上传后的 ms:// 引用"
                    )
        return messages
