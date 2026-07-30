"""Tests for provider pipelines — 路由、请求翻译、流式解析（fake chunk，不打真实 API）。"""

from types import SimpleNamespace

import pytest

from floodmind.agent.native.providers import (
    route_pipeline,
    DashScopePipeline,
    DeepSeekPipeline,
    KimiPipeline,
    MiniMaxPipeline,
    OpenAICompatiblePipeline,
)
from floodmind.agent.native.providers.base import (
    ProviderPipeline,
    incremental,
    split_think_tags,
)


def _ns(**kwargs):
    """用 SimpleNamespace 模拟 openai SDK 的 delta/chunk 对象。"""
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# 共享工具
# ---------------------------------------------------------------------------

class TestSharedUtils:
    def test_incremental_passthrough(self):
        buf, inc = incremental("", "abc")
        assert (buf, inc) == ("abc", "abc")
        buf, inc = incremental(buf, "def")
        assert (buf, inc) == ("abcdef", "def")

    def test_incremental_cumulative(self):
        """累积式全量帧 → 只取差分。"""
        buf, inc = incremental("", "思考")
        assert inc == "思考"
        buf, inc = incremental(buf, "思考过程")
        assert (buf, inc) == ("思考过程", "过程")

    def test_split_think_simple(self):
        answer, reasoning = split_think_tags("<think>想</think>答")
        assert (answer, reasoning) == ("答", "想")

    def test_split_think_unclosed(self):
        answer, reasoning = split_think_tags("<think>还在想")
        assert (answer, reasoning) == ("", "还在想")

    def test_split_think_partial_tag_withheld(self):
        """末尾不完整标签片段暂扣，不计入任何一侧。"""
        answer, reasoning = split_think_tags("你好<thi")
        assert (answer, reasoning) == ("你好", "")
        answer, reasoning = split_think_tags("<think>想</thi")
        assert (answer, reasoning) == ("", "想")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

class TestRouting:
    @pytest.mark.parametrize("pid,mid,url,expected,conservative", [
        # provider id / base_url 精确命中 → 完整适配
        ("dashscope", "qwen3.6-plus", "https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope", False),
        ("qwen", "qwen3.7-plus", "https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope", False),
        ("deepseek", "deepseek-v4-pro", "https://api.deepseek.com", "deepseek", False),
        ("minimax", "MiniMax-M3", "https://api.minimaxi.com/v1", "minimax", False),
        ("moonshot", "kimi-k2.6", "https://api.moonshot.cn/v1", "kimi", False),
        # 连线方言优先：deepseek provider 但 base_url 是百炼 → dashscope
        ("deepseek", "deepseek-v4-pro", "https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope", False),
        # 聚合网关：仅模型名前缀命中 → 保守模式（解析适配开、请求适配标准）
        ("openai", "MiniMax/MiniMax-M3", "https://gw.example.com/v1", "minimax", True),
        ("openai", "kimi/kimi-k3", "https://gw.example.com/v1", "kimi", True),
        ("openai", "deepseek-v4-flash", "https://gw.example.com/v1", "deepseek", True),
        # 未知 → 兜底
        ("openai", "gpt-4o", "https://api.openai.com/v1", "openai-compatible", False),
        ("ollama", "llama3", "http://localhost:11434/v1", "openai-compatible", False),
        ("", "some-model", "", "openai-compatible", False),
    ])
    def test_route(self, pid, mid, url, expected, conservative):
        p = route_pipeline(pid, mid, url)
        assert p.name == expected
        assert p.conservative is conservative


# ---------------------------------------------------------------------------
# 请求参数翻译
# ---------------------------------------------------------------------------

def _base_params(model="m"):
    return {
        "model": model,
        "messages": [],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": True,
    }


class TestPrepareRequest:
    def test_fallback_stream_options_only(self):
        p = OpenAICompatiblePipeline()
        out = p.prepare_request(_base_params(), enable_thinking=True, stream=True)
        assert out["stream_options"] == {"include_usage": True}
        assert "extra_body" not in out  # enable_thinking 硬编码已移除

    def test_dashscope_enable_thinking(self):
        p = DashScopePipeline()
        out = p.prepare_request(_base_params("qwen3.6-plus"), enable_thinking=True, stream=True)
        assert out["extra_body"]["enable_thinking"] is True
        assert "max_completion_tokens" in out and "max_tokens" not in out

    def test_dashscope_thinking_off_no_param(self):
        p = DashScopePipeline()
        out = p.prepare_request(_base_params("qwen3.6-plus"), enable_thinking=False, stream=True)
        assert "extra_body" not in out

    def test_dashscope_minimax_hosted_uses_thinking_type(self):
        """百炼托管的 MiniMax/xxx 模型用 thinking.type 而非 enable_thinking。"""
        p = DashScopePipeline()
        out = p.prepare_request(_base_params("MiniMax/MiniMax-M3"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "adaptive"}
        assert "enable_thinking" not in out["extra_body"]

    def test_dashscope_thinking_downgrades_forced_tool_choice(self):
        p = DashScopePipeline()
        params = _base_params("qwen3.6-plus")
        params["tool_choice"] = {"type": "function", "function": {"name": "f"}}
        out = p.prepare_request(params, enable_thinking=True, stream=True)
        assert out["tool_choice"] == "auto"

    def test_deepseek_thinking_dialect(self):
        p = DeepSeekPipeline()
        out = p.prepare_request(_base_params("deepseek-v4-pro"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "enabled"}
        # 思考模式剥离采样参数
        assert "temperature" not in out

    def test_deepseek_thinking_off_keeps_temperature(self):
        p = DeepSeekPipeline()
        out = p.prepare_request(_base_params("deepseek-v4-pro"), enable_thinking=False, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "disabled"}
        assert out["temperature"] == 0.3

    def test_kimi_k26_full_adaptation(self):
        p = KimiPipeline()
        out = p.prepare_request(_base_params("kimi-k2.6"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "enabled", "keep": "all"}
        assert "temperature" not in out  # k2.6 禁传
        assert "max_completion_tokens" in out

    def test_kimi_k3_no_thinking_param(self):
        p = KimiPipeline()
        out = p.prepare_request(_base_params("kimi-k3"), enable_thinking=True, stream=True)
        assert "extra_body" not in out  # k3 始终思考，无开关
        assert "temperature" not in out  # k 系列 temperature 锁死，统一剥离

    def test_kimi_k25_temperature_stripped(self):
        """实测 k2.5 同样仅允许 temperature=1，显式传入 400。"""
        p = KimiPipeline()
        out = p.prepare_request(_base_params("kimi-k2.5"), enable_thinking=False, stream=True)
        assert "temperature" not in out

    def test_kimi_k27_never_disabled(self):
        p = KimiPipeline()
        out = p.prepare_request(_base_params("kimi-k2.7-code"), enable_thinking=False, stream=True)
        assert "extra_body" not in out  # 强制思考，关闭只省略
        assert "temperature" not in out

    def test_minimax_thinking_split(self):
        p = MiniMaxPipeline()
        out = p.prepare_request(_base_params("MiniMax-M3"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "adaptive"}
        assert out["extra_body"]["reasoning_split"] is True
        assert "max_completion_tokens" in out

    def test_minimax_m3_disable(self):
        p = MiniMaxPipeline()
        out = p.prepare_request(_base_params("MiniMax-M3"), enable_thinking=False, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "disabled"}

    def test_minimax_m2_never_disabled(self):
        p = MiniMaxPipeline()
        out = p.prepare_request(_base_params("MiniMax-M2.7"), enable_thinking=False, stream=True)
        assert "extra_body" not in out  # M2.x 不可发 disabled

    def test_minimax_temperature_clamped(self):
        p = MiniMaxPipeline()
        params = _base_params("MiniMax-M3")
        params["temperature"] = 5.0
        out = p.prepare_request(params, enable_thinking=False, stream=True)
        assert out["temperature"] == 2.0

    def test_conservative_mode_standard_request(self):
        """聚合网关命中模型前缀 → 请求适配退化为标准行为。"""
        p = route_pipeline("openai", "MiniMax/MiniMax-M3", "https://gw.example.com/v1")
        assert p.conservative is True
        out = p.prepare_request(_base_params("MiniMax/MiniMax-M3"), enable_thinking=True, stream=True)
        assert "extra_body" not in out
        assert "max_tokens" in out  # 不重命名

    def test_explicit_extra_body_wins(self):
        """调用方显式 extra_body 优先级最高（setdefault 不覆盖）。"""
        p = MiniMaxPipeline()
        params = _base_params("MiniMax-M3")
        params["extra_body"] = {"thinking": {"type": "disabled"}, "custom": 1}
        out = p.prepare_request(params, enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "disabled"}
        assert out["extra_body"]["custom"] == 1
        assert out["extra_body"]["reasoning_split"] is True  # 未显式给的厂商参数仍注入


# ---------------------------------------------------------------------------
# 流式解析
# ---------------------------------------------------------------------------

class TestStreamParsing:
    def test_standard_reasoning_content(self):
        p = OpenAICompatiblePipeline()
        state = p.new_stream_state()
        assert p.extract_reasoning(_ns(reasoning_content="想"), state) == "想"
        assert p.extract_reasoning(_ns(reasoning_content=None, reasoning="想2"), state) == "想2"

    def test_cumulative_reasoning_dedup(self):
        """累积式全量帧只发差分。"""
        p = OpenAICompatiblePipeline()
        state = p.new_stream_state()
        assert p.extract_reasoning(_ns(reasoning_content="思考"), state) == "思考"
        assert p.extract_reasoning(_ns(reasoning_content="思考过程"), state) == "过程"

    def test_minimax_reasoning_details_cumulative(self):
        p = MiniMaxPipeline()
        state = p.new_stream_state()
        d1 = _ns(reasoning_content=None, reasoning_details=[{"text": "步骤一"}])
        d2 = _ns(reasoning_content=None, reasoning_details=[{"text": "步骤一，步骤二"}])
        assert p.extract_reasoning(d1, state) == "步骤一"
        assert p.extract_reasoning(d2, state) == "，步骤二"

    def test_minimax_think_tag_streaming(self):
        """content 内 <think> 标签跨 chunk 剥离：思考进 reasoning，回答干净。"""
        p = MiniMaxPipeline()
        state = p.new_stream_state()
        chunks = ["<thi", "nk>分析一下", "水位</think>", "答案是", " 32.5m<thi"]
        reasoning_out, answer_out = "", ""
        for c in chunks:
            a, r = p.filter_content(c, state)
            answer_out += a
            reasoning_out += r
        assert reasoning_out == "分析一下水位"
        assert answer_out == "答案是 32.5m"

    def test_minimax_plain_content_passthrough(self):
        p = MiniMaxPipeline()
        state = p.new_stream_state()
        a, r = p.filter_content("普通回答", state)
        assert (a, r) == ("普通回答", "")

    def test_kimi_usage_in_choices(self):
        """Kimi 流式 usage 在末帧 choices[0].usage（非标位置）。"""
        p = KimiPipeline()
        chunk = _ns(
            usage=None,
            choices=[_ns(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})],
        )
        assert p.extract_usage(chunk) == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }

    def test_standard_usage_top_level(self):
        p = OpenAICompatiblePipeline()
        chunk = _ns(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}, choices=[])
        assert p.extract_usage(chunk)["total_tokens"] == 3

    def test_usage_missing_returns_none(self):
        p = MiniMaxPipeline()
        assert p.extract_usage(_ns(usage=None, choices=[])) is None


# ---------------------------------------------------------------------------
# 消息适配
# ---------------------------------------------------------------------------

class TestPrepareMessages:
    def test_kimi_rejects_public_image_url(self):
        p = KimiPipeline()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
        }]
        with pytest.raises(ValueError, match="Kimi 不支持公网 URL 图片"):
            p.prepare_messages(messages)

    def test_kimi_allows_base64_image(self):
        p = KimiPipeline()
        messages = [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}],
        }]
        assert p.prepare_messages(messages) is messages

    def test_minimax_passthrough(self):
        p = MiniMaxPipeline()
        messages = [{
            "role": "user",
            "content": [{"type": "video_url", "video_url": {"url": "mm_file://fid", "fps": 1}}],
        }]
        assert p.prepare_messages(messages) is messages
