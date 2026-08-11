"""DashScope（阿里云百炼）codec。

方言要点（docs/qwen.txt）：
- 思考开关：``extra_body={"enable_thinking": True}``（Qwen/GLM/Kimi 等自研与托管模型）
- 例外：稀宇直供 ``MiniMax/xxx`` 模型用 ``thinking: {"type": "adaptive"/"disabled"}``
- 思考模式不支持 tool_choice 强制指定函数 → 降级为 auto
- max_tokens 语义不含思维链且将废弃 → max_completion_tokens
- usage：末帧空 choices chunk 的顶层 usage（标准）
- 多模态 block：image_url / video_url / video / input_audio（控制项原样透传）
"""

from typing import Any, Dict

from .base import ProviderCodec


class DashScopeCodec(ProviderCodec):
    name = "dashscope"
    uses_max_completion_tokens = True

    @classmethod
    def match(cls, provider_id: str, model_id: str, base_url: str) -> int:
        url = (base_url or "").lower()
        if "dashscope" in url or "aliyuncs.com" in url:
            return 100
        if (provider_id or "").lower() in ("dashscope", "qwen", "aliyun"):
            return 60
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

        # 思考模式不支持强制指定函数调用
        if enable_thinking and isinstance(params.get("tool_choice"), dict):
            params["tool_choice"] = "auto"

        model = str(params.get("model", "")).lower()
        extra = dict(params.get("extra_body") or {})
        if model.startswith("minimax/"):
            # 稀宇直供模型：思考开关用 thinking.type（qwen.txt L67）
            extra.setdefault("thinking", {"type": "adaptive" if enable_thinking else "disabled"})
        elif enable_thinking:
            extra.setdefault("enable_thinking", True)
        if extra:
            params["extra_body"] = extra
        return params
