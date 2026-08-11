"""Tests for provider codecs — 路由、请求翻译、流式解析（fake chunk，不打真实 API）。"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from floodmind.agent.native.providers import (
    route_codec,
    DashScopeCodec,
    DeepSeekCodec,
    KimiCodec,
    MiniMaxCodec,
    OpenAICompatibleCodec,
)
from floodmind.agent.native.providers.base import (
    ProviderCodec,
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
        p = route_codec(pid, mid, url)
        assert p.name == expected
        assert p.conservative is conservative

    @pytest.mark.parametrize("base_url", [
        "https://api.anthropic.com",
        "https://API.ANTHROPIC.COM/v1/",
        "https://api.anthropic.com./v1",
    ])
    def test_official_anthropic_endpoint_rejected(self, base_url):
        from floodmind.agent.native.model_client import ModelClient

        with pytest.raises(ValueError, match="OpenAI-compatible gateway"):
            ModelClient(
                api_key="k",
                base_url=base_url,
                model_name="claude-sonnet-4-5",
                provider="anthropic",
            )

    @pytest.mark.parametrize("provider", ["anthropic", "custom", "openai"])
    def test_claude_model_allowed_via_custom_openai_gateway(self, provider):
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(
            api_key="k",
            base_url="https://claude-gateway.example.com/v1",
            model_name="claude-sonnet-4-5",
            provider=provider,
        )
        assert client.pipeline.name == "openai-compatible"


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
        p = OpenAICompatibleCodec()
        out = p.prepare_request(_base_params(), enable_thinking=True, stream=True)
        assert out["stream_options"] == {"include_usage": True}
        assert "extra_body" not in out  # enable_thinking 硬编码已移除

    def test_dashscope_enable_thinking(self):
        p = DashScopeCodec()
        out = p.prepare_request(_base_params("qwen3.6-plus"), enable_thinking=True, stream=True)
        assert out["extra_body"]["enable_thinking"] is True
        assert "max_completion_tokens" in out and "max_tokens" not in out

    def test_dashscope_thinking_off_no_param(self):
        p = DashScopeCodec()
        out = p.prepare_request(_base_params("qwen3.6-plus"), enable_thinking=False, stream=True)
        assert "extra_body" not in out

    def test_dashscope_minimax_hosted_uses_thinking_type(self):
        """百炼托管的 MiniMax/xxx 模型用 thinking.type 而非 enable_thinking。"""
        p = DashScopeCodec()
        out = p.prepare_request(_base_params("MiniMax/MiniMax-M3"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "adaptive"}
        assert "enable_thinking" not in out["extra_body"]

    def test_dashscope_thinking_downgrades_forced_tool_choice(self):
        p = DashScopeCodec()
        params = _base_params("qwen3.6-plus")
        params["tool_choice"] = {"type": "function", "function": {"name": "f"}}
        out = p.prepare_request(params, enable_thinking=True, stream=True)
        assert out["tool_choice"] == "auto"

    def test_deepseek_thinking_dialect(self):
        p = DeepSeekCodec()
        out = p.prepare_request(_base_params("deepseek-v4-pro"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "enabled"}
        # 思考模式剥离采样参数
        assert "temperature" not in out

    def test_deepseek_thinking_off_keeps_temperature(self):
        p = DeepSeekCodec()
        out = p.prepare_request(_base_params("deepseek-v4-pro"), enable_thinking=False, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "disabled"}
        assert out["temperature"] == 0.3

    def test_kimi_k26_full_adaptation(self):
        p = KimiCodec()
        out = p.prepare_request(_base_params("kimi-k2.6"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "enabled", "keep": "all"}
        assert "temperature" not in out  # k2.6 禁传
        assert "max_completion_tokens" in out

    def test_kimi_k3_no_thinking_param(self):
        p = KimiCodec()
        out = p.prepare_request(_base_params("kimi-k3"), enable_thinking=True, stream=True)
        assert "extra_body" not in out  # k3 始终思考，无 thinking 开关
        assert out["reasoning_effort"] == "max"  # K3 顶层推理强度，默认 max
        assert "temperature" not in out  # k 系列 temperature 锁死，统一剥离

    def test_kimi_k25_temperature_stripped(self):
        """实测 k2.5 同样仅允许 temperature=1，显式传入 400。"""
        p = KimiCodec()
        out = p.prepare_request(_base_params("kimi-k2.5"), enable_thinking=False, stream=True)
        assert "temperature" not in out

    def test_kimi_k27_never_disabled(self):
        p = KimiCodec()
        out = p.prepare_request(_base_params("kimi-k2.7-code"), enable_thinking=False, stream=True)
        assert "extra_body" not in out  # 强制思考，关闭只省略
        assert "temperature" not in out

    def test_kimi_k27_thinking_on_no_param(self):
        p = KimiCodec()
        out = p.prepare_request(_base_params("kimi-k2.7-code"), enable_thinking=True, stream=True)
        assert "extra_body" not in out  # 实测 k2.7-code 无需 thinking 参数

    def test_kimi_thinking_downgrades_required_tool_choice(self):
        p = KimiCodec()
        params = _base_params("kimi-k2.6")
        params["tools"] = [{"type": "function", "function": {"name": "f"}}]
        params["tool_choice"] = "required"
        out = p.prepare_request(params, enable_thinking=True, stream=True)
        assert out["tool_choice"] == "auto"

        params = _base_params("kimi-k2.7-code")
        params["tools"] = [{"type": "function", "function": {"name": "f"}}]
        params["tool_choice"] = "required"
        out = p.prepare_request(params, enable_thinking=True, stream=True)
        assert out["tool_choice"] == "auto"

    def test_kimi_reasoning_content_passback_snapshot(self):
        p = KimiCodec()
        state = p.new_stream_state()
        acc = {"role": "assistant", "content": ""}
        d1 = _ns(role="assistant", reasoning_content="先分析", content=None)
        d2 = _ns(reasoning_content="先分析再调用", content=None)
        assert p.extract_reasoning(d1, state) == "先分析"
        p.capture_assistant_delta(d1, state, acc)
        assert p.extract_reasoning(d2, state) == "再调用"
        p.capture_assistant_delta(d2, state, acc)
        p.capture_assistant_delta(_ns(content="我来调用工具"), state, acc)
        msg = p.build_assistant_message(acc, [{"id": "c1", "name": "search", "arguments": '{"q":"x"}'}])
        assert msg["role"] == "assistant"
        assert msg["content"] == "我来调用工具"
        assert msg["reasoning_content"] == "先分析再调用"
        assert msg["tool_calls"][0]["function"]["arguments"] == '{"q":"x"}'

    def test_kimi_pipeline_records_routing_context(self):
        p = route_codec("moonshot", "kimi-k3", "https://api.moonshot.cn/v1")
        assert p.provider_id == "moonshot"
        assert p.model_id == "kimi-k3"
        assert p.base_url == "https://api.moonshot.cn/v1"

    @pytest.mark.parametrize(
        "pipeline",
        [DashScopeCodec(), KimiCodec(), MiniMaxCodec()],
    )
    def test_completion_token_capability_rewrites_shared_field(self, pipeline):
        out = pipeline.prepare_request(
            _base_params("provider-model"), enable_thinking=False, stream=False
        )
        assert out["max_completion_tokens"] == 4096
        assert "max_tokens" not in out

    def test_minimax_thinking_split(self):
        p = MiniMaxCodec()
        out = p.prepare_request(_base_params("MiniMax-M3"), enable_thinking=True, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "adaptive"}
        assert out["extra_body"]["reasoning_split"] is True
        assert "max_completion_tokens" in out

    def test_minimax_m3_disable(self):
        p = MiniMaxCodec()
        out = p.prepare_request(_base_params("MiniMax-M3"), enable_thinking=False, stream=True)
        assert out["extra_body"]["thinking"] == {"type": "disabled"}

    def test_minimax_m2_never_disabled(self):
        p = MiniMaxCodec()
        out = p.prepare_request(_base_params("MiniMax-M2.7"), enable_thinking=False, stream=True)
        assert "extra_body" not in out  # M2.x 不可发 disabled

    def test_minimax_temperature_clamped(self):
        p = MiniMaxCodec()
        params = _base_params("MiniMax-M3")
        params["temperature"] = 5.0
        out = p.prepare_request(params, enable_thinking=False, stream=True)
        assert out["temperature"] == 2.0

    def test_conservative_mode_standard_request(self):
        """聚合网关命中模型前缀 → 请求适配退化为标准行为。"""
        p = route_codec("openai", "MiniMax/MiniMax-M3", "https://gw.example.com/v1")
        assert p.conservative is True
        out = p.prepare_request(_base_params("MiniMax/MiniMax-M3"), enable_thinking=True, stream=True)
        assert "extra_body" not in out
        assert "max_tokens" in out  # 不重命名

    def test_explicit_extra_body_wins(self):
        """调用方显式 extra_body 优先级最高（setdefault 不覆盖）。"""
        p = MiniMaxCodec()
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
    def test_reasoning_delta_does_not_drop_colocated_content_tool_or_finish(self):
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(api_key="k", base_url="https://example.com/v1", model_name="m")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([
            _ns(choices=[_ns(
                delta=_ns(
                    reasoning_content="think",
                    content="answer",
                    tool_calls=[_ns(
                        index=0, id="c1", function=_ns(name="search", arguments='{"q":"x"}')
                    )],
                ),
                finish_reason="tool_calls",
            )])
        ])
        client._client = mock_client
        events = list(client.stream_chat(messages=[{"role": "user", "content": "x"}]))
        assert next(e.content for e in events if e.type == "reasoning") == "think"
        assert next(e.content for e in events if e.type == "token") == "answer"
        assert next(e for e in events if e.type == "tool_call_done").tool_call.arguments == {"q": "x"}
        assert next(e for e in events if e.type == "done").terminal_reason.code == "tool_calls"

    def test_standard_reasoning_content(self):
        p = OpenAICompatibleCodec()
        state = p.new_stream_state()
        assert p.extract_reasoning(_ns(reasoning_content="想"), state) == "想"
        assert p.extract_reasoning(_ns(reasoning_content=None, reasoning="想2"), state) == "想2"

    def test_cumulative_reasoning_dedup(self):
        """累积式全量帧只发差分。"""
        p = OpenAICompatibleCodec()
        state = p.new_stream_state()
        assert p.extract_reasoning(_ns(reasoning_content="思考"), state) == "思考"
        assert p.extract_reasoning(_ns(reasoning_content="思考过程"), state) == "过程"

    def test_minimax_reasoning_details_cumulative(self):
        p = MiniMaxCodec()
        state = p.new_stream_state()
        acc = {"role": "assistant", "content": ""}
        d1 = _ns(reasoning_content=None, reasoning_details=[{"type": "reasoning.text", "text": "步骤一"}])
        d2 = _ns(reasoning_content=None, reasoning_details=[{"type": "reasoning.text", "text": "步骤一，步骤二"}])
        assert p.extract_reasoning(d1, state) == "步骤一"
        p.capture_assistant_delta(d1, state, acc)
        assert p.extract_reasoning(d2, state) == "，步骤二"
        p.capture_assistant_delta(d2, state, acc)
        msg = p.build_assistant_message(acc, [])
        assert msg["reasoning_content"] == "步骤一，步骤二"
        assert msg["reasoning_details"] == [{"type": "reasoning.text", "text": "步骤一，步骤二"}]

    def test_minimax_preserves_raw_think_content_for_passback(self):
        p = MiniMaxCodec()
        state = p.new_stream_state()
        acc = {"role": "assistant", "content": ""}
        chunks = ["<think>想", "一下</think>答"]
        answer, reasoning = "", ""
        for c in chunks:
            delta = _ns(content=c, reasoning_content=None, reasoning_details=None)
            a, r = p.filter_content(c, state)
            p.capture_assistant_delta(delta, state, acc)
            answer += a
            reasoning += r
        assert answer == "答"
        assert reasoning == "想一下"
        msg = p.build_assistant_message(acc, [])
        assert msg["content"] == "<think>想一下</think>答"

    def test_minimax_think_tag_streaming(self):
        """content 内 <think> 标签跨 chunk 剥离：思考进 reasoning，回答干净。"""
        p = MiniMaxCodec()
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
        p = MiniMaxCodec()
        state = p.new_stream_state()
        a, r = p.filter_content("普通回答", state)
        assert (a, r) == ("普通回答", "")

    def test_kimi_usage_in_choices(self):
        """Kimi 流式 usage 在末帧 choices[0].usage（非标位置）。"""
        p = KimiCodec()
        chunk = _ns(
            usage=None,
            choices=[_ns(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})],
        )
        assert p.extract_usage(chunk) == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }

    def test_standard_usage_top_level(self):
        p = OpenAICompatibleCodec()
        chunk = _ns(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}, choices=[])
        assert p.extract_usage(chunk)["total_tokens"] == 3

    def test_usage_missing_returns_none(self):
        p = MiniMaxCodec()
        assert p.extract_usage(_ns(usage=None, choices=[])) is None

    def test_stream_emits_final_cumulative_usage(self):
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(api_key="k", base_url="https://example.com/v1", model_name="m")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([
            _ns(
                usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
                choices=[_ns(delta=_ns(content="a", tool_calls=None), finish_reason=None)],
            ),
            _ns(
                usage={"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                choices=[_ns(delta=_ns(content=None, tool_calls=None), finish_reason="stop")],
            ),
        ])
        client._client = mock_client

        events = list(client.stream_chat(messages=[{"role": "user", "content": "x"}]))
        usage_events = [json.loads(e.content) for e in events if e.type == "usage"]
        assert usage_events == [
            {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}
        ]

    def test_abort_preserves_aborted_terminal_reason(self):
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(api_key="k", base_url="https://example.com/v1", model_name="m")
        stream = MagicMock()
        stream.__iter__.return_value = iter([
            _ns(usage=None, choices=[_ns(delta=_ns(content="late", tool_calls=None), finish_reason=None)])
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = stream
        client._client = mock_client

        events = list(client.stream_chat(
            messages=[{"role": "user", "content": "x"}],
            abort_check=lambda: True,
        ))
        done = next(e for e in events if e.type == "done")
        assert done.terminal_reason.code == "aborted"
        assert done.terminal_reason.raw == "aborted"
        stream.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# 消息适配
# ---------------------------------------------------------------------------

class TestPrepareMessages:
    def test_kimi_rejects_public_image_url(self):
        p = KimiCodec()
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
        p = KimiCodec()
        messages = [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}],
        }]
        assert p.prepare_messages(messages) is messages

    def test_minimax_passthrough(self):
        p = MiniMaxCodec()
        messages = [{
            "role": "user",
            "content": [{"type": "video_url", "video_url": {"url": "mm_file://fid", "fps": 1}}],
        }]
        assert p.prepare_messages(messages) is messages


# ---------------------------------------------------------------------------
# ToolCall id 对齐（流式 fallback id 写回 accumulator）
# ---------------------------------------------------------------------------

class TestToolCallIdAlignment:
    def test_stream_empty_tool_call_id_aligns_with_assistant_message(self):
        """流式 tool call id 为空时，fallback id 写回 accumulator，保证 assistant 消息
        tool_calls[].id 与 ToolCall.id / 工具结果 tool_call_id 一致。
        根因：此前 ToolCall 用 fallback id 但 assistant 消息仍读空 acc["id"]，
        MiniMax 等厂商校验报 'tool result's tool id not found (2013)'。"""
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(
            api_key="k",
            base_url="https://api.minimaxi.com/v1",
            model_name="MiniMax-M3",
            provider="minimax",
        )
        stream = [
            _ns(choices=[_ns(
                delta=_ns(
                    role="assistant",
                    content=None,
                    reasoning_content=None,
                    tool_calls=[_ns(
                        index=0,
                        id="",
                        function=_ns(name="get_weather", arguments='{"city":"上海"}'),
                    )],
                ),
                finish_reason=None,
            )]),
            _ns(choices=[_ns(delta=_ns(content=None, reasoning_content=None, tool_calls=None), finish_reason="tool_calls")]),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(stream)
        client._client = mock_client

        events = list(client.stream_chat(messages=[{"role": "user", "content": "hi"}]))
        tool_call_event = next(e for e in events if e.type == "tool_call_done")
        done_event = next(e for e in events if e.type == "assistant_message_done")

        tc_id = tool_call_event.tool_call.id
        assert tc_id.startswith("call_"), f"fallback id 未生成: {tc_id!r}"
        hist_tc_id = done_event.raw["message"]["tool_calls"][0]["id"]
        assert hist_tc_id == tc_id, "assistant 消息 tool_calls[].id 与 ToolCall.id 不一致"

    def test_stream_with_provider_id_preserved(self):
        """provider 给了非空 id 时保持原样，不生成 fallback。"""
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(
            api_key="k",
            base_url="https://api.minimaxi.com/v1",
            model_name="MiniMax-M3",
            provider="minimax",
        )
        stream = [
            _ns(choices=[_ns(
                delta=_ns(
                    role="assistant",
                    content=None,
                    reasoning_content=None,
                    tool_calls=[_ns(
                        index=0,
                        id="real_id_42",
                        function=_ns(name="get_weather", arguments='{"city":"上海"}'),
                    )],
                ),
                finish_reason=None,
            )]),
            _ns(choices=[_ns(delta=_ns(content=None, reasoning_content=None, tool_calls=None), finish_reason="tool_calls")]),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(stream)
        client._client = mock_client

        events = list(client.stream_chat(messages=[{"role": "user", "content": "hi"}]))
        tool_call_event = next(e for e in events if e.type == "tool_call_done")
        done_event = next(e for e in events if e.type == "assistant_message_done")
        assert tool_call_event.tool_call.id == "real_id_42"
        assert done_event.raw["message"]["tool_calls"][0]["id"] == "real_id_42"

    def test_malformed_nonempty_tool_json_never_emits_executable_call(self):
        """Malformed args must not become {} and trigger a tool's default arguments."""
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(api_key="k", base_url="https://example.com/v1", model_name="m")
        stream = [
            _ns(choices=[_ns(
                delta=_ns(content=None, tool_calls=[_ns(
                    index=0, id="bad1", function=_ns(name="side_effect", arguments='{"path":')
                )]),
                finish_reason=None,
            )]),
            _ns(choices=[_ns(delta=_ns(content=None, tool_calls=None), finish_reason="tool_calls")]),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(stream)
        client._client = mock_client

        events = list(client.stream_chat(messages=[{"role": "user", "content": "go"}]))
        assert not [e for e in events if e.type == "tool_call_done"]
        invalid = next(e for e in events if e.type == "invalid_tool_call")
        assert invalid.invalid_tool_call.name == "side_effect"
        assert invalid.invalid_tool_call.raw_arguments == '{"path":'
        done = next(e for e in events if e.type == "done")
        assert done.terminal_reason.code == "tool_calls"
        assert done.terminal_reason.raw == "tool_calls"

    @pytest.mark.parametrize("raw,code", [
        ("stop", "completed"),
        ("length", "max_tokens"),
        ("content_filter", "filtered"),
        ("refusal", "refused"),
        ("pause_turn", "paused"),
    ])
    def test_stream_preserves_raw_terminal_reason(self, raw, code):
        from floodmind.agent.native.model_client import ModelClient

        client = ModelClient(api_key="k", base_url="https://example.com/v1", model_name="m")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([
            _ns(choices=[_ns(delta=_ns(content=None, tool_calls=None), finish_reason=raw)])
        ])
        client._client = mock_client
        done = next(e for e in client.stream_chat(messages=[{"role": "user", "content": "x"}]) if e.type == "done")
        assert done.terminal_reason.code == code
        assert done.terminal_reason.raw == raw

