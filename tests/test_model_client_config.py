"""ModelClient 的显式上下文窗口声明：自定义模型无需进入 catalog。"""
import pytest

from floodmind.agent.native.model_client import ModelClient


def _make(**kwargs):
    return ModelClient(
        api_key="test-key",
        base_url="http://127.0.0.1:3000/v1",
        model_name="custom-model",
        **kwargs,
    )


def test_context_window_defaults_to_none():
    assert _make().context_window is None


def test_context_window_is_stored():
    assert _make(context_window=128_000).context_window == 128_000


@pytest.mark.parametrize("bad", [0, -1, True, "100", 1.5])
def test_context_window_must_be_positive_int(bad):
    with pytest.raises(ValueError):
        _make(context_window=bad)
