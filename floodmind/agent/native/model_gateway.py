"""模型路由与回退（G-19）。

职责边界：
- 回退只在**本轮尚未输出任何事件**时发生；已开始输出后绝不切换模型，
  避免同一响应出现两个模型的片段（蓝图 08 §7.2/§7.3）。
- 回退链由配置显式声明（``model_routing.fallbacks``），不做隐式猜测；
  目标模型必须能在 providers 目录中解析，否则启动即报错。
- 每次回退都通过 ``on_fallback`` 记录（事件总线 + 日志），可观测。
- 子代理模型由 ``model_routing.subagent_model`` 显式指定；未配置时继承
  主代理模型，不改变现有行为。

配置（settings.json）::

    "model_routing": {
      "subagent_model": "",
      "fallbacks": ["qwen3.6-plus"],
      "max_fallbacks": 2
    }
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)

FallbackObserver = Callable[[str, str, str], None]


class FallbackModelClient:
    """把主模型与显式回退链包装成同一个 ModelClient 接口。

    - 成功使用回退后保持在该模型（同一 run 内行为一致）；
    - 每次 run 开始由 executor 调用 ``reset_fallbacks()`` 重新从主模型尝试；
    - 全部失败时抛最后一个异常，并复位到主模型，交给 executor 的重试策略。
    """

    def __init__(
        self,
        primary: Any,
        fallbacks: Iterable[Any] = (),
        *,
        on_fallback: Optional[FallbackObserver] = None,
        max_fallbacks: int = 2,
    ):
        clients = [primary, *list(fallbacks)]
        cap = max(0, int(max_fallbacks))
        self._clients: List[Any] = clients[: 1 + cap]
        self._active = 0
        self._on_fallback = on_fallback

    # ── ModelClient 接口 ────────────────────────────────────────

    @property
    def model_name(self) -> str:
        return getattr(self._clients[self._active], "model_name", "")

    def reset_fallbacks(self) -> None:
        self._active = 0

    def stream_chat(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        start = self._active
        last_error: Optional[Exception] = None
        for index in range(start, len(self._clients)):
            client = self._clients[index]
            emitted = False
            try:
                for event in client.stream_chat(*args, **kwargs):
                    emitted = True
                    yield event
                self._active = index
                return
            except Exception as exc:
                if emitted:
                    # 已产生输出：重试/切换都会重复内容或重复扣费，原样上抛。
                    raise
                last_error = exc
                if index + 1 >= len(self._clients):
                    break
                self._record_switch(client, self._clients[index + 1], exc)
                self._active = index + 1
        # 整条链都失败：复位到入口模型，让 executor 的外层重试从主模型重新开始。
        self._active = start
        if last_error is None:  # 空链不可达（构造时至少含 primary），保守返回。
            return
        raise last_error

    def classify_error(self, exc: Exception):
        return self._clients[self._active].classify_error(exc)

    def __getattr__(self, name: str) -> Any:
        # 透传 api_key / temperature / invoke / chat 等非流式接口到当前模型。
        if name in {"_clients", "_active", "_on_fallback"}:
            raise AttributeError(name)
        return getattr(self._clients[self._active], name)

    # ── 记录 ────────────────────────────────────────────────────

    def _record_switch(self, source: Any, target: Any, exc: Exception) -> None:
        from_model = getattr(source, "model_name", "")
        to_model = getattr(target, "model_name", "")
        error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning(
            "[模型网关] 回退 %s -> %s（%s）", from_model, to_model, error
        )
        if self._on_fallback is not None:
            try:
                self._on_fallback(from_model, to_model, error)
            except Exception as record_error:
                logger.warning("[模型网关] 回退事件记录失败: %s", record_error)


# ── 配置驱动的构造 ─────────────────────────────────────────────


def routing_config() -> dict:
    from floodmind.config.settings import get_config

    raw = get_config().get("model_routing", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("settings.json 的 model_routing 必须是对象")
    return raw


def _fallback_keys(cfg: dict) -> List[str]:
    raw = cfg.get("fallbacks", [])
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(k, str) and k.strip() for k in raw):
        raise ValueError("model_routing.fallbacks 必须是非空模型 id 字符串数组")
    return [k.strip() for k in raw]


def _max_fallbacks(cfg: dict) -> int:
    raw = cfg.get("max_fallbacks", 2)
    try:
        cap = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("model_routing.max_fallbacks 必须是整数") from exc
    return max(0, cap)


def build_fallback_client(
    primary: Any,
    *,
    wrap: Optional[Callable[[Any], Any]] = None,
    on_fallback: Optional[FallbackObserver] = None,
) -> Any:
    """按配置给 primary 包上回退链；无配置时原样返回。"""
    cfg = routing_config()
    keys = _fallback_keys(cfg)
    cap = _max_fallbacks(cfg)
    if not keys or cap == 0:
        return primary

    from floodmind.agent.native.model_client import ModelClient

    fallbacks = []
    for key in keys[:cap]:
        client = ModelClient.from_settings_with_preset(model_key=key)
        fallbacks.append(wrap(client) if wrap else client)
    return FallbackModelClient(
        primary,
        fallbacks,
        on_fallback=on_fallback,
        max_fallbacks=cap,
    )


def build_subagent_model_client(
    parent_client: Any,
    *,
    wrap: Optional[Callable[[Any], Any]] = None,
    on_fallback: Optional[FallbackObserver] = None,
) -> Any:
    """子代理模型：显式 subagent_model 优先，否则继承父模型；再套回退链。"""
    cfg = routing_config()
    key = str(cfg.get("subagent_model", "") or "").strip()
    if key:
        from floodmind.agent.native.model_client import ModelClient

        base = ModelClient.from_settings_with_preset(model_key=key)
    else:
        base = parent_client
    if wrap is not None:
        base = wrap(base)
    return build_fallback_client(base, wrap=wrap, on_fallback=on_fallback)
