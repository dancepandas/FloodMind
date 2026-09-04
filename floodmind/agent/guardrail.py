"""Guardrail 输入/输出校验契约。

对标 openai-agents 的 InputGuardrail/OutputGuardrail，适配 FloodMind 同步
runtime：顺序执行（不并行）、任一 tripwire 即停。宿主通过 ``Agent`` 构造
参数 ``input_guardrails`` / ``output_guardrails`` 注入，specialist 继承主
agent 的 guardrail（安全策略不因委派而放宽）。

用法::

    def no_secrets(messages: List[dict]) -> GuardrailResult:
        ...
        return GuardrailResult(tripwire_triggered=True, message="包含敏感信息")

    agent = Agent(llm=llm, input_guardrails=[no_secrets])

注意：输入 guardrail 校验**完整消息列表**（每次 LLM 调用前），输出 guardrail
校验**最终答案**（产出时，含 max_tokens 续写拼接）。两者应职责分离——同一
可调用对象不宜同时注册为输入与输出 guardrail：输出重试轮会把 tripped 答案
注入消息列表，重叠注册会让输入闸在修正轮先行拦截。
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, runtime_checkable


@dataclass
class GuardrailResult:
    """guardrail 执行结果。

    - ``tripwire_triggered=True``：输入侧终止本次 run；输出侧先重试一次；
    - ``message``：拦截理由（输入侧成为 final_output；输出侧作为修正提示注入）；
    - ``replaced_input``：仅输入侧有效——改写后的完整消息列表（宿主应返回
      深拷贝，executor 采纳后会在运行中原地演化该列表），非空时替换本次
      调用的输入（脱敏/上下文注入等场景）。
    """
    tripwire_triggered: bool = False
    message: str = ""
    replaced_input: Optional[List[dict]] = None


@runtime_checkable
class InputGuardrail(Protocol):
    """输入 guardrail：每次 LLM 调用前对完整消息列表执行。"""

    def __call__(self, messages: List[dict]) -> GuardrailResult: ...


@runtime_checkable
class OutputGuardrail(Protocol):
    """输出 guardrail：最终答案（含续写拼接）产出时执行，工具轮次跳过。

    第二个参数（run state）可选：单参 ``def g(output)`` 与双参
    ``def g(output, state)`` 均可。
    """

    def __call__(self, output: str, state: Any = None) -> GuardrailResult: ...


def guardrail_name(guardrail: Any) -> str:
    """取 guardrail 的展示名（函数名/类名），用于事件与日志。"""
    fn = getattr(guardrail, "__func__", guardrail)
    return getattr(fn, "__name__", "") or type(guardrail).__name__ or "guardrail"


_ARITY_TAKES_STATE: dict = {}


def _effective_param_count(guardrail: Any) -> Optional[int]:
    """可调用对象的「调用方可传」位置参数个数（绑定语义已解析）。

    绑定方法取 __func__ 签名并减 1（self 不占调用方参数）；functools.partial
    按已绑定位置参数折减、已绑定 keyword 使同名形参不再接受位置传参；普通
    函数直接取签名。不可内省返回 None。
    """
    try:
        fn = guardrail
        offset = 0
        bound_keywords = frozenset()
        # 绑定方法：__func__ 签名含 self，调用方少传一个
        if hasattr(guardrail, "__func__") and hasattr(guardrail, "__self__"):
            fn = guardrail.__func__
            offset = 1
        elif isinstance(guardrail, functools.partial):
            fn = guardrail.func
            offset = len(guardrail.args)
            bound_keywords = frozenset(guardrail.keywords or {})
        sig = inspect.signature(fn)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        # keyword 绑定的形参已被 partial 消化，不再计入可传位置参数
        count = sum(1 for p in positional if p.name not in bound_keywords) - offset
        return count
    except (TypeError, ValueError):
        return None


def _call_shape(guardrail: Any, phase: str) -> str:
    """缓存化调用形态判定。返回 "two_pos"（位置双参）/"kw"（state 走关键字）/"one"。

    绑定方法/partial 的绑定语义由 _effective_param_count 折减。
    """
    key = id(guardrail)
    if key in _ARITY_TAKES_STATE:
        return _ARITY_TAKES_STATE[key]
    try:
        fn2 = guardrail
        if hasattr(guardrail, "__func__") and hasattr(guardrail, "__self__"):
            fn2 = guardrail.__func__
        elif isinstance(guardrail, functools.partial):
            fn2 = guardrail.func
        params = list(inspect.signature(fn2).parameters.values())
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        state_param = next(
            (p for p in params
             if p.name == "state"
             and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            inspect.Parameter.KEYWORD_ONLY)),
            None)
    except (TypeError, ValueError):
        params = None
        has_var_keyword = False
        state_param = None

    if params is None:
        # 不可内省：按 phase 的安全回退（输入单参、输出双参）
        shape = "two_pos" if phase == "output" else "one"
    else:
        count = _effective_param_count(guardrail)
        remaining = count if count is not None else 0
        if has_var_keyword:
            # **kwargs 吃关键字 state；但剩余位置参数必须恰好只有 arg 槽，
            # 否则 "out" 会错位落到 state 位置槽
            shape = "kw" if remaining <= 1 else "two_pos"
        elif state_param is not None:
            # 有命名 state 形参：若位置槽仅剩 arg 一个（state 的位置槽已被
            # partial/绑定消化），state 走关键字；否则走位置
            shape = "kw" if remaining <= 1 else "two_pos"
        else:
            shape = "two_pos" if remaining >= 2 else "one"

    _ARITY_TAKES_STATE[key] = shape
    return shape


def _call_guardrail(guardrail: Any, arg: Any, state: Any = None,
                    phase: str = "output") -> GuardrailResult:
    """调用 guardrail，适配参数个数并兜住一切宿主侧故障（fail-closed）。

    输入 guardrail 单参（messages）、输出 guardrail 单参/双参均可。绑定方法
    与 partial 的绑定语义由 _effective_param_count 折减。异常与非法返回值
    （非 GuardrailResult）一律转 tripwire，宿主代码故障不炸状态机。
    """
    try:
        shape = _call_shape(guardrail, phase)
        if shape == "two_pos":
            result = guardrail(arg, state)
        elif shape == "kw":
            result = guardrail(arg, state=state)
        else:
            result = guardrail(arg)
    except Exception as exc:
        return GuardrailResult(
            tripwire_triggered=True,
            message=f"guardrail 执行异常: {exc}",
        )
    if result is None:
        return GuardrailResult(tripwire_triggered=False)
    if not isinstance(result, GuardrailResult):
        return GuardrailResult(
            tripwire_triggered=True,
            message=f"guardrail 返回类型非法（应为 GuardrailResult）: {type(result).__name__}",
        )
    return result


def run_input_guardrails(guardrails, messages: List[dict]):
    """顺序执行输入 guardrail，任一 tripwire 即短路返回。

    返回 ``(result, tripped_name)``；``tripped_name`` 为触发者展示名，
    未触发时为 None。链式组合：每个后续 guardrail 收到前一个的
    ``replaced_input``（无替换时为原始列表）；最终采纳最后一个非空替换。
    """
    current = messages
    replaced: Optional[List[dict]] = None
    for guardrail in guardrails or []:
        result = _call_guardrail(guardrail, current, phase="input")
        if result.tripwire_triggered:
            return result, guardrail_name(guardrail)
        if result.replaced_input is not None:
            current = result.replaced_input
            replaced = result.replaced_input
    return GuardrailResult(tripwire_triggered=False, replaced_input=replaced), None


def run_output_guardrails(guardrails, output: str, state: Any = None):
    """顺序执行输出 guardrail，任一 tripwire 即短路返回。

    返回 ``(result, tripped_name)``。
    """
    for guardrail in guardrails or []:
        result = _call_guardrail(guardrail, output, state, phase="output")
        if result.tripwire_triggered:
            return result, guardrail_name(guardrail)
    return GuardrailResult(tripwire_triggered=False), None
