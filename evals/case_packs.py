"""确定性 case pack 生成器（评测集扩容 C1）。

与 ``evals/cases/*.json`` 的分工：JSON 用例承载少量高价值手工场景，case pack 用
参数化家族系统地扩展覆盖面（中断点、迭代上界、token 预算边界、连续失败、非法参数
形态、终态、DOOM LOOP、真实工具安全边界等）。所有用例都是纯数据，结构与 JSON 用例
完全一致，可直接被 ``evals.runner.run_case`` 消费。

设计约束：

- 确定性：纯函数生成，无随机、无时间、无环境依赖；同一版本恒生成同一集合；
- 可解释：每个用例都有中文 description 说明验证的契约点；
- 真实断言：每个用例至少一个 expect 断言，不凑数；
- ID 全局唯一：以 ``<tier>-pack-<family>`` 为前缀，与 JSON 用例不冲突；
- held-out：``heldout=true`` 的用例默认不加载，仅 ``--heldout`` 时参与运行，
  用于定期换血/校准，避免评测集被针对性过拟合。

与执行器语义对齐的关键约定（写家族时必须遵守）：

- DOOM LOOP（同工具同参数连续 3 次）在工具执行**之前**检测，优先级高于连续失败
  终止（5 次）；要走到 5 次连续失败，必须让每次调用参数不同（签名不同）；
- scripted 执行器按调用 id（``tool_results``）优先、工具名（``tools``）兜底查结果，
  同名工具的多轮不同结果必须用 ``tool_results`` 按调用 id 声明；
- 中断（abort_check）在每轮流式消费完成后生效：第 k 次调用后中断 = 前 k-1 轮完整
  执行 + 第 k 轮整轮丢弃。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

# ── 事件/结构辅助 ───────────────────────────────────────────────


def _token(content: str) -> Dict[str, Any]:
    return {"type": "token", "content": content}


def _reasoning(content: str) -> Dict[str, Any]:
    return {"type": "reasoning", "content": content}


def _usage(prompt_tokens: int, completion_tokens: int, total_tokens: int) -> Dict[str, Any]:
    return {
        "type": "usage",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def _call(call_id: str, name: str, arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "tool_call_done",
        "tool_call": {"id": call_id, "name": name, "arguments": arguments or {}},
    }


def _invalid(call_id: str, name: str, raw_arguments: str, error: str) -> Dict[str, Any]:
    return {
        "type": "invalid_tool_call",
        "invalid_tool_call": {
            "id": call_id,
            "name": name,
            "raw_arguments": raw_arguments,
            "error": error,
        },
    }


def _done(terminal_reason: str) -> Dict[str, Any]:
    return {"type": "done", "terminal_reason": terminal_reason}


def _tool_round(*events: Dict[str, Any]) -> Dict[str, Any]:
    """一轮工具调用：若干事件后接 done(tool_calls)。"""
    return {"events": [*events, _done("tool_calls")]}


def _answer_round(content: str) -> Dict[str, Any]:
    return {"events": [_token(content), _done("stop")]}


def _tool_spec(
    description: str,
    content: str,
    status: str = "completed",
    parameters: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "description": description,
        "parameters": parameters or {"type": "object", "properties": {}},
        "result": {"content": content, "status": status},
    }


def _call_result(content: str, status: str = "completed") -> Dict[str, Any]:
    """按调用 id 声明的 scripted 结果（优先于按工具名声明的结果）。"""
    return {"content": content, "status": status}


_GAUGE_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {"station": {"type": "string"}},
    "required": ["station"],
}

# 固定事件序列：中断/终止族中"不应到达"的兜底轮（若中断失效会触发脚本断言）。
_UNREACHED = [_token("不应到达这一轮。"), _done("stop")]


def _case(
    case_id: str,
    tier: str,
    description: str,
    user_text: str,
    rounds: List[Dict[str, Any]],
    expect: Dict[str, Any],
    tools: Dict[str, Any] | None = None,
    tool_results: Dict[str, Dict[str, Any]] | None = None,
    max_iterations: int = 10,
    max_total_tokens: int = 0,
    abort_after_model_calls: int | None = None,
    tool_mode: str | None = None,
    workspace_files: Dict[str, str] | None = None,
    mode: str | None = None,
    heldout: bool = False,
) -> Dict[str, Any]:
    case: Dict[str, Any] = {
        "id": case_id,
        "tier": tier,
        "description": description,
        "user_text": user_text,
        "max_iterations": max_iterations,
        "tools": tools or {},
        "rounds": rounds,
        "expect": expect,
    }
    if tool_results:
        case["tool_results"] = tool_results
    if max_total_tokens:
        case["max_total_tokens"] = max_total_tokens
    if abort_after_model_calls is not None:
        case["abort_after_model_calls"] = abort_after_model_calls
    if tool_mode:
        case["tool_mode"] = tool_mode
    if workspace_files:
        case["workspace_files"] = workspace_files
    if mode:
        case["mode"] = mode
    if heldout:
        case["heldout"] = True
    return case


# ── smoke pack：主循环基本行为 ─────────────────────────────────


def _smoke_pack() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # 1. usage 事件与最终答案同轮：计费事件不影响 stop 终态。
    cases.append(_case(
        "smoke-pack-usage-event",
        "smoke",
        "usage 事件：单轮内同时出现计费事件与最终答案，completed 终态不受 usage 影响。",
        "直接给出结论。",
        [{"events": [_usage(40, 60, 100), _token("带计费的直接回答。"), _done("stop")]}],
        {
            "final_output_contains": ["直接回答"],
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 2. 两轮工具顺序执行：第一轮结果必须出现在第二轮模型上下文。
    cases.append(_case(
        "smoke-pack-two-round-tools",
        "smoke",
        "两轮工具：第一轮结果回填到第二轮上下文，第二轮结果进入最终答案。",
        "先查水位，再查流量，最后汇总。",
        [
            _tool_round(_call("c1", "QueryLevel", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryFlow", {"station": "敖江"})),
            _answer_round("水位 12.3 m，流量 800 m³/s。"),
        ],
        {
            "final_output_contains": ["12.3", "800"],
            "model_calls": 3,
            "tool_calls": ["QueryLevel", "QueryFlow"],
            "tool_statuses": ["completed", "completed"],
            "prompt_contains": [
                {"call_index": 1, "contains": "水位 12.3 m"},
                {"call_index": 2, "contains": "流量 800 m³/s"},
            ],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
        },
    ))

    # 3. 两轮多工具：每轮并行两工具，共 4 次调用按序进入工具执行器。
    cases.append(_case(
        "smoke-pack-two-round-multi-tool",
        "smoke",
        "两轮多工具：每轮同轮并行两个工具，共 4 次调用按序执行，结果全部回填。",
        "两轮并行查询水位和流量。",
        [
            _tool_round(
                _call("c1", "QueryLevel", {"station": "敖江"}),
                _call("c2", "QueryFlow", {"station": "敖江"}),
            ),
            _tool_round(
                _call("c3", "QueryLevel", {"station": "闽江"}),
                _call("c4", "QueryFlow", {"station": "闽江"}),
            ),
            _answer_round("两轮查询全部完成。"),
        ],
        {
            "final_output_contains": ["全部完成"],
            "model_calls": 3,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryLevel", "QueryFlow"],
            "tool_statuses": ["completed"] * 4,
            "prompt_contains": [
                {"call_index": 2, "contains": "流量 800 m³/s"},
            ],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
        },
    ))

    # 4. 工具错误后恢复：同一工具失败一次后重试成功（按调用 id 声明不同结果）。
    cases.append(_case(
        "smoke-pack-tool-error-then-success",
        "smoke",
        "工具错误后恢复：同一工具第一次调用失败、第二次同参数重试成功，错误结果先回填上下文。",
        "查询遥测水位，失败就重试。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryLink", {"station": "敖江"})),
            _answer_round("链路恢复，当前水位 12.3 m。"),
        ],
        {
            "final_output_contains": ["链路恢复"],
            "model_calls": 3,
            "tool_calls": ["QueryLink", "QueryLink"],
            "tool_statuses": ["error", "completed"],
            "invalid_calls": 1,
            "prompt_contains": [
                {"call_index": 1, "contains": "遥测链路超时"},
                {"call_index": 2, "contains": "链路恢复"},
            ],
        },
        tools={
            "QueryLink": _tool_spec("查询遥测链路水位。", "占位", parameters=_GAUGE_PARAMS),
        },
        tool_results={
            "c1": _call_result("错误：遥测链路超时", status="error"),
            "c2": _call_result("链路恢复，水位 12.3 m"),
        },
    ))

    # 5. 非法工具参数后恢复：invalid 反馈回填，下一轮用合法参数调用工具。
    cases.append(_case(
        "smoke-pack-invalid-args-then-tool",
        "smoke",
        "非法工具参数后恢复：参数 JSON 无法解析的反馈进入下一轮上下文，随后合法调用成功。",
        "查询敖江站水位。",
        [
            {"events": [
                _invalid("bad1", "ReadGauge", '{"station": ', "Unexpected end of JSON input"),
                _done("tool_calls"),
            ]},
            _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
            _answer_round("已修正参数：敖江站水位 12.3 m。"),
        ],
        {
            "final_output_contains": ["已修正参数"],
            "model_calls": 3,
            "tool_calls": ["ReadGauge"],
            "tool_statuses": ["completed"],
            "prompt_contains": [
                {"call_index": 1, "contains": "参数 JSON 无法解析"},
                {"call_index": 2, "contains": "水位 12.3 m"},
            ],
        },
        tools={
            "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
        },
    ))

    # 6. 空工具结果：completed 但内容为空，模型据此说明无数据。
    cases.append(_case(
        "smoke-pack-empty-tool-result",
        "smoke",
        "空工具结果：工具正常返回但内容为空串，循环继续且最终答案说明无数据。",
        "查询审计日志。",
        [
            _tool_round(_call("c1", "QueryAudit", {"day": "2026-10-19"})),
            _answer_round("审计没有返回任何内容。"),
        ],
        {
            "final_output_contains": ["没有返回任何内容"],
            "model_calls": 2,
            "tool_calls": ["QueryAudit"],
            "tool_statuses": ["completed"],
        },
        tools={
            "QueryAudit": _tool_spec("查询审计日志。", ""),
        },
    ))

    # 7. 长文本最终答案：大段 token 拼接后原样进入 final_output。
    long_text = "洪水预报长文段落。" * 120  # 约 1300 字
    cases.append(_case(
        "smoke-pack-long-final-answer",
        "smoke",
        "长文本答案：单轮输出约千字长文，首尾片段都完整进入最终答案。",
        "写一份尽可能长的汛情说明。",
        [{"events": [_token(long_text), _done("stop")]}],
        {
            "final_output_equals": long_text,
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 8. reasoning + answer：推理事件与答案同轮，答案照常落 final_output。
    cases.append(_case(
        "smoke-pack-reasoning-then-answer",
        "smoke",
        "reasoning+answer：同轮先推理后作答，推理内容不混入最终答案文本。",
        "判断当前水位是否超警戒。",
        [{"events": [
            _reasoning("先比较 12.3 m 与警戒水位 13.0 m。"),
            _token("结论：水位正常，未超警戒。"),
            _done("stop"),
        ]}],
        {
            "final_output_equals": "结论：水位正常，未超警戒。",
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 9. 多轮 usage 事件：工具轮与答案轮都带 usage，无预算时循环正常 completed。
    cases.append(_case(
        "smoke-pack-usage-across-rounds",
        "smoke",
        "多轮 usage 事件：工具轮与答案轮各自上报 usage，无预算时循环正常结束。",
        "查水位并总结。",
        [
            {"events": [_usage(30, 10, 40), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(20, 5, 25), _token("敖江站水位 12.3 m。"), _done("stop")]},
        ],
        {
            "final_output_contains": ["12.3"],
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "tool_statuses": ["completed"],
        },
        tools={
            "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
        },
    ))

    # 10. 答案拆成多个 token 事件：拼接顺序进入 final_output。
    cases.append(_case(
        "smoke-pack-token-split-answer",
        "smoke",
        "多 token 拼接：一个答案拆成三个 token 事件，按序拼接为最终答案。",
        "分段输出结论。",
        [{"events": [
            _token("第一段。"),
            _token("第二段。"),
            _token("第三段。"),
            _done("stop"),
        ]}],
        {
            "final_output_equals": "第一段。第二段。第三段。",
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 11. 空最终答案：模型直接 stop 且无任何 token，final_output 为空串。
    cases.append(_case(
        "smoke-pack-empty-final-answer",
        "smoke",
        "空答案边界：模型无输出即 stop，最终答案为空串且终态 completed。",
        "这个不用回答。",
        [{"events": [_done("stop")]}],
        {
            "final_output_equals": "",
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 12. 跨轮回填：第二轮看到第一轮结果，第三轮同时看到两轮结果。
    cases.append(_case(
        "smoke-pack-cross-round-backfill",
        "smoke",
        "工具结果跨轮回填：第二轮上下文包含第一轮结果，第三轮同时包含两轮结果。",
        "先查上游，再查下游，最后汇总。",
        [
            _tool_round(_call("c1", "QueryUpstream", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryDownstream", {"station": "敖江"})),
            _answer_round("上下游汇总完成。"),
        ],
        {
            "final_output_contains": ["汇总完成"],
            "model_calls": 3,
            "tool_calls": ["QueryUpstream", "QueryDownstream"],
            "prompt_contains": [
                {"call_index": 1, "contains": "上游水位 10.0 m"},
                {"call_index": 2, "contains": "上游水位 10.0 m"},
                {"call_index": 2, "contains": "下游水位 8.0 m"},
            ],
        },
        tools={
            "QueryUpstream": _tool_spec("查询上游水位。", "上游水位 10.0 m", parameters=_GAUGE_PARAMS),
            "QueryDownstream": _tool_spec("查询下游水位。", "下游水位 8.0 m", parameters=_GAUGE_PARAMS),
        },
    ))

    # 13. 同工具不同参数两轮：参数不同不触发 DOOM LOOP，两轮结果分别回填。
    cases.append(_case(
        "smoke-pack-same-tool-diff-args",
        "smoke",
        "同工具不同参数：两轮调用同一工具但参数不同（签名不同），不触发死循环检测，"
        "两轮结果按调用 id 区分并回填。",
        "分别查询敖江站和闽江站。",
        [
            _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
            _tool_round(_call("c2", "ReadGauge", {"station": "闽江"})),
            _answer_round("两站水位已汇总。"),
        ],
        {
            "final_output_contains": ["两站水位"],
            "model_calls": 3,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "tool_statuses": ["completed", "completed"],
            "prompt_contains": [
                {"call_index": 2, "contains": "闽江站水位 8.1 m"},
            ],
        },
        tools={
            "ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS),
        },
        tool_results={
            "c1": _call_result("敖江站水位 12.3 m"),
            "c2": _call_result("闽江站水位 8.1 m"),
        },
    ))

    # 14. 推理与答案交错：reasoning/token 交错事件流照常拼接。
    cases.append(_case(
        "smoke-pack-interleaved-reasoning",
        "smoke",
        "推理与答案交错：reasoning 与 token 事件交错出现，答案只由 token 拼接。",
        "分析后作答。",
        [{"events": [
            _reasoning("步骤一："),
            _token("结论 A。"),
            _reasoning("步骤二："),
            _token("结论 B。"),
            _done("stop"),
        ]}],
        {
            "final_output_equals": "结论 A。结论 B。",
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    # 15. 单轮三工具并行：三个工具同轮调度，结果全部回填。
    cases.append(_case(
        "smoke-pack-three-tools-one-round",
        "smoke",
        "单轮三工具：同一轮并行请求三个工具，三者按序执行且结果都进入下一轮上下文。",
        "同时查询水位、流量和雨量。",
        [
            _tool_round(
                _call("c1", "QueryLevel", {"station": "敖江"}),
                _call("c2", "QueryFlow", {"station": "敖江"}),
                _call("c3", "QueryRain", {"station": "敖江"}),
            ),
            _answer_round("三项指标查询完毕。"),
        ],
        {
            "final_output_contains": ["查询完毕"],
            "model_calls": 2,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryRain"],
            "tool_statuses": ["completed"] * 3,
            "prompt_contains": [
                {"call_index": 1, "contains": "水位 12.3 m"},
                {"call_index": 1, "contains": "流量 800 m³/s"},
                {"call_index": 1, "contains": "雨量 5 mm"},
            ],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
            "QueryRain": _tool_spec("查询雨量。", "雨量 5 mm", parameters=_GAUGE_PARAMS),
        },
    ))

    # 16. 工具轮带叙述文本：同轮先输出文本再调用工具，文本进入助手消息而非最终答案。
    cases.append(_case(
        "smoke-pack-tool-round-with-preamble",
        "smoke",
        "工具轮叙述文本：工具调用同轮的 token 进入助手消息（后续上下文可见），"
        "最终答案只含末轮文本。",
        "查询水位。",
        [
            {"events": [
                _token("我先查一下水位。"),
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _done("tool_calls"),
            ]},
            _answer_round("敖江站水位 12.3 m。"),
        ],
        {
            "final_output_equals": "敖江站水位 12.3 m。",
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "prompt_contains": [
                {"call_index": 1, "contains": "我先查一下水位。"},
                {"call_index": 1, "contains": "水位 12.3 m"},
            ],
        },
        tools={
            "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
        },
    ))

    # 17. 工具轮只有 reasoning：推理内容进推理通道，最终答案只含答案轮文本。
    cases.append(_case(
        "smoke-pack-tool-round-pure-reasoning",
        "smoke",
        "工具轮纯推理：工具调用同轮只出现 reasoning 事件，推理不混入最终答案，"
        "工具照常执行且结果回填。",
        "查询水位。",
        [
            {"events": [
                _reasoning("先查水位再决定下一步。"),
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _done("tool_calls"),
            ]},
            _answer_round("已查询：水位 12.3 m。"),
        ],
        {
            "final_output_equals": "已查询：水位 12.3 m。",
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "prompt_contains": [{"call_index": 1, "contains": "水位 12.3 m"}],
        },
        tools={
            "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
        },
    ))

    # 18. usage 事件在答案 token 之后：同轮事件顺序不影响答案拼接。
    cases.append(_case(
        "smoke-pack-usage-after-answer-token",
        "smoke",
        "usage 位置边界：答案 token 之后再出现 usage 事件，最终答案仍只由 token 组成。",
        "给出结论。",
        [{"events": [
            _token("结论：水位正常。"),
            _usage(10, 5, 15),
            _done("stop"),
        ]}],
        {
            "final_output_equals": "结论：水位正常。",
            "model_calls": 1,
            "tool_calls": [],
        },
    ))

    return cases


# ── tool pack：真实文件工具 ────────────────────────────────────


def _tool_pack() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # 1. Edit 已有文件：字符串替换落盘，内容校验。
    cases.append(_case(
        "tool-pack-edit-existing-file",
        "tool",
        "真实 Edit：对已有 fixture 做精确字符串替换，workspace 内容更新且结果回填上下文。",
        "把记录里的水位改成 13.5。",
        [
            _tool_round(_call("c1", "Edit", {
                "file_path": "notes/水位.md",
                "old_string": "12.3",
                "new_string": "13.5",
            })),
            _answer_round("已把水位更新为 13.5。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Edit"],
            "tool_statuses": ["completed"],
            "invalid_calls": 0,
            "workspace_contains": {"notes/水位.md": "当前水位 13.5 m"},
            "prompt_contains": [{"call_index": 1, "contains": "文件编辑成功"}],
            "final_output_contains": ["13.5"],
        },
        tool_mode="real",
        workspace_files={"notes/水位.md": "# 记录\n\n当前水位 12.3 m。\n"},
        tools={"Edit": {}},
    ))

    # 2. Write 嵌套目录：自动创建多级父目录。
    cases.append(_case(
        "tool-pack-write-nested-dirs",
        "tool",
        "真实 Write 嵌套目录：写入深层路径时自动创建父目录，落盘内容可校验。",
        "把摘要写进深层目录。",
        [
            _tool_round(_call("c1", "Write", {
                "file_path": "reports/2026/10/summary.md",
                "content": "# 摘要\n敖江站水位 12.3 m。\n",
            })),
            _answer_round("报告已写入。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Write"],
            "tool_statuses": ["completed"],
            "workspace_contains": {"reports/2026/10/summary.md": "敖江站水位 12.3 m"},
            "prompt_contains": [{"call_index": 1, "contains": "文件写入成功"}],
            "final_output_contains": ["已写入"],
        },
        tool_mode="real",
        tools={"Write": {}},
    ))

    # 3. Write Unicode 后 Read 回读：非 ASCII 内容无损往返。
    cases.append(_case(
        "tool-pack-write-unicode-readback",
        "tool",
        "真实 Write/Read Unicode：中文与特殊符号写入后读回，内容无损且回填上下文。",
        "写入摘要再读回确认。",
        [
            _tool_round(_call("c1", "Write", {
                "file_path": "数据/摘要.md",
                "content": "中文摘要：水位 12.3 m，状态 Ω 正常。\n",
            })),
            _tool_round(_call("c2", "Read", {"file_path": "数据/摘要.md"})),
            _answer_round("回读确认内容一致。"),
        ],
        {
            "model_calls": 3,
            "tool_calls": ["Write", "Read"],
            "tool_statuses": ["completed", "completed"],
            "workspace_contains": {"数据/摘要.md": "状态 Ω 正常"},
            "prompt_contains": [{"call_index": 2, "contains": "状态 Ω 正常"}],
            "final_output_contains": ["确认"],
        },
        tool_mode="real",
        tools={"Write": {}, "Read": {}},
    ))

    # 4. Read 缺失文件：错误文本回填上下文，循环继续。
    cases.append(_case(
        "tool-pack-read-missing-file",
        "tool",
        "真实 Read 缺失文件：工具返回文件不存在错误文本（软错误），模型据上下文作答。",
        "读取不存在的文件并说明。",
        [
            _tool_round(_call("c1", "Read", {"file_path": "ghost.md"})),
            _answer_round("ghost.md 不存在，无法总结。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Read"],
            "tool_statuses": ["error"],
            "prompt_contains": [{"call_index": 1, "contains": "文件不存在"}],
            "final_output_contains": ["不存在"],
        },
        tool_mode="real",
        tools={"Read": {}},
    ))

    # 5. Glob 无匹配：未找到文本回填，循环继续。
    cases.append(_case(
        "tool-pack-glob-no-match",
        "tool",
        "真实 Glob 无匹配：返回未找到匹配文件文本，模型据此说明而不是当作硬失败。",
        "找出所有 .xyz 文件。",
        [
            _tool_round(_call("c1", "Glob", {"pattern": "**/*.xyz"})),
            _answer_round("没有任何 .xyz 文件。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Glob"],
            "tool_statuses": ["completed"],
            "prompt_contains": [{"call_index": 1, "contains": "未找到匹配文件"}],
            "final_output_contains": ["没有任何"],
        },
        tool_mode="real",
        workspace_files={"docs/a.md": "正文\n"},
        tools={"Glob": {}},
    ))

    # 6. Grep 无匹配：未找到匹配内容文本回填。
    cases.append(_case(
        "tool-pack-grep-no-match",
        "tool",
        "真实 Grep 无匹配：返回未找到匹配内容文本，模型据此作答。",
        "搜一下资料里有没有这个测站。",
        [
            _tool_round(_call("c1", "Grep", {"pattern": "不存在的测站"})),
            _answer_round("没有找到该测站记录。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Grep"],
            "tool_statuses": ["completed"],
            "prompt_contains": [{"call_index": 1, "contains": "未找到匹配内容"}],
            "final_output_contains": ["没有找到"],
        },
        tool_mode="real",
        workspace_files={"docs/a.md": "敖江站水位 12.3 m\n"},
        tools={"Grep": {}},
    ))

    # 7. 连续读两个文件：顺序读取，第二个文件的上下文包含第一个文件的内容。
    cases.append(_case(
        "tool-pack-read-two-files",
        "tool",
        "真实 Read 连读两文件：顺序读取两个 fixture，第三轮上下文同时包含两份文件内容。",
        "读两个测站文件并汇总。",
        [
            _tool_round(_call("c1", "Read", {"file_path": "站A.md"})),
            _tool_round(_call("c2", "Read", {"file_path": "站B.md"})),
            _answer_round("两站水位已汇总。"),
        ],
        {
            "model_calls": 3,
            "tool_calls": ["Read", "Read"],
            "tool_statuses": ["completed", "completed"],
            "prompt_contains": [
                {"call_index": 1, "contains": "站A 水位 12.3 m"},
                {"call_index": 2, "contains": "站B 水位 8.1 m"},
            ],
            "final_output_contains": ["汇总"],
        },
        tool_mode="real",
        workspace_files={
            "站A.md": "站A 水位 12.3 m\n",
            "站B.md": "站B 水位 8.1 m\n",
        },
        tools={"Read": {}},
    ))

    # 8. Edit 不存在文件：错误文本回填，不产副作用。
    cases.append(_case(
        "tool-pack-edit-missing-file",
        "tool",
        "真实 Edit 缺失文件：返回文件不存在错误文本，workspace 不产生新文件。",
        "编辑一个不存在的文件。",
        [
            _tool_round(_call("c1", "Edit", {
                "file_path": "missing.md",
                "old_string": "x",
                "new_string": "y",
            })),
            _answer_round("目标文件不存在，无法编辑。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Edit"],
            "tool_statuses": ["error"],
            "prompt_contains": [{"call_index": 1, "contains": "文件不存在"}],
            "final_output_contains": ["不存在"],
            "workspace_not_contains": ["missing.md"],
        },
        tool_mode="real",
        tools={"Edit": {}},
    ))

    return cases


# ── regression pack：参数化家族 ────────────────────────────────


def _regression_pack() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # A. 用户中断在不同模型调用点生效（调用点 1 已有 JSON 用例，此处覆盖 2..10）。
    # 每轮参数带 step 序号：相同参数连续 3 次会先触发 DOOM LOOP，干扰中断断言。
    for k in range(2, 11):
        rounds: List[Dict[str, Any]] = [
            _tool_round(_call(f"c{i}", "SlowTask", {"step": i})) for i in range(1, k)
        ]
        rounds.append({"events": list(_UNREACHED)})
        cases.append(_case(
            f"regression-pack-abort-at-call-{k}",
            "regression",
            f"用户中断：abort 在第 {k} 次模型调用后生效，此前 {k - 1} 轮工具正常执行，"
            f"第 {k} 轮整轮丢弃且终态文案含“中断”。",
            "开始一个较长任务。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["SlowTask"] * (k - 1),
                "final_output_contains": ["中断"],
            },
            tools={"SlowTask": _tool_spec("执行耗时任务。", "阶段性结果")},
            abort_after_model_calls=k,
        ))

    # B. max_iterations 上界（1 已有 JSON 用例，此处覆盖 2..8）。
    # 每轮参数带 attempt 序号，避免相同参数触发 DOOM LOOP 干扰截断断言。
    for k in range(2, 9):
        rounds = [
            _tool_round(_call(f"c{i}", "ReadGauge", {"station": "敖江", "attempt": i}))
            for i in range(1, k + 1)
        ]
        cases.append(_case(
            f"regression-pack-max-iterations-{k}",
            "regression",
            f"迭代上界：max_iterations={k} 时恰好执行 {k} 轮工具后截断，"
            f"终态回退到最后一次工具结果原文。",
            "不断查询，不要停。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["ReadGauge"] * k,
                "tool_statuses": ["completed"] * k,
                "final_output_contains": [f"第 {k} 轮水位 {k}.0 m"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS)},
            tool_results={
                f"c{i}": _call_result(f"第 {i} 轮水位 {i}.0 m") for i in range(1, k + 1)
            },
            max_iterations=k,
        ))

    budget_tools = {"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)}

    # C1 预算恰好在最终答案轮达到：不再发起新调用，completed 不受影响。
    cases.append(_case(
        "regression-pack-budget-exact-on-final-answer",
        "regression",
        "token 预算边界：累计用量恰好在最终答案轮达到预算，stop 终态正常 completed，"
        "不出现预算截断文案。",
        "直接给结论。",
        [{"events": [_usage(40, 60, 100), _token("结论：水位正常。"), _done("stop")]}],
        {
            "model_calls": 1,
            "tool_calls": [],
            "final_output_contains": ["水位正常"],
            "final_output_not_contains": ["预算上限"],
        },
        max_total_tokens=100,
    ))

    # C2 预算在工具轮后恰好达到：下一轮调用前截断。
    cases.append(_case(
        "regression-pack-budget-exact-after-tool",
        "regression",
        "token 预算边界：两轮 usage 累计恰好在第二轮工具后达到预算 100，"
        "不再发起第三次模型调用，终态文案给出累计用量与上限。",
        "循环查询直到得到结论。",
        [
            {"events": [_usage(40, 20, 60), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(30, 10, 40), _call("c2", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "final_output_contains": ["预算上限 100", "累计 token 用量 100"],
        },
        tools=budget_tools,
        max_total_tokens=100,
    ))

    # C3 三轮累计达到 150。
    cases.append(_case(
        "regression-pack-budget-150-three-rounds",
        "regression",
        "token 预算边界：三轮各 50 token 累计达到预算 150，第三轮工具执行后截断。",
        "分三轮查询。",
        [
            {"events": [_usage(30, 20, 50), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(30, 20, 50), _call("c2", "ReadGauge", {"station": "闽江"}), _done("tool_calls")]},
            {"events": [_usage(30, 20, 50), _call("c3", "ReadGauge", {"station": "九龙江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 3,
            "tool_calls": ["ReadGauge"] * 3,
            "final_output_contains": ["预算上限 150", "累计 token 用量 150"],
        },
        tools=budget_tools,
        max_total_tokens=150,
    ))

    # C4 边界 200（60+60+80）。
    cases.append(_case(
        "regression-pack-budget-200-three-rounds",
        "regression",
        "token 预算边界：60+60+80 累计达到预算 200，第三轮后截断，文案给出精确数字。",
        "分三轮查询。",
        [
            {"events": [_usage(40, 20, 60), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(40, 20, 60), _call("c2", "ReadGauge", {"station": "闽江"}), _done("tool_calls")]},
            {"events": [_usage(50, 30, 80), _call("c3", "ReadGauge", {"station": "九龙江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 3,
            "tool_calls": ["ReadGauge"] * 3,
            "final_output_contains": ["预算上限 200", "累计 token 用量 200"],
        },
        tools=budget_tools,
        max_total_tokens=200,
    ))

    # C5 预算未超：正常 completed。
    cases.append(_case(
        "regression-pack-budget-under-completes",
        "regression",
        "token 预算边界：两轮累计 60 远低于预算 100，循环正常 completed。",
        "查水位并总结。",
        [
            {"events": [_usage(20, 10, 30), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(15, 15, 30), _token("水位 12.3 m，正常。"), _done("stop")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "final_output_contains": ["正常"],
            "final_output_not_contains": ["预算上限"],
        },
        tools=budget_tools,
        max_total_tokens=100,
    ))

    # C6 同轮多个 usage 事件：最后一笔覆盖本轮（step_tokens 覆盖语义）。
    cases.append(_case(
        "regression-pack-budget-last-usage-wins",
        "regression",
        "token 预算边界：同一轮出现两笔 usage 事件时以最后一笔为本轮用量（30 被 50 覆盖），"
        "累计 100 达到预算截断。",
        "查询并继续。",
        [
            {"events": [
                _usage(20, 10, 30),
                _usage(35, 15, 50),
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _done("tool_calls"),
            ]},
            {"events": [_usage(30, 20, 50), _call("c2", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "final_output_contains": ["预算上限 100", "累计 token 用量 100"],
        },
        tools=budget_tools,
        max_total_tokens=100,
    ))

    # D. 连续工具失败 1..4 次后恢复：同一工具、参数每次不同（避免 DOOM LOOP 抢先）。
    for k in range(1, 5):
        rounds = [
            _tool_round(_call(f"c{i}", "ReadGauge", {"station": "敖江", "attempt": i}))
            for i in range(1, k + 1)
        ]
        rounds.append(_tool_round(_call(f"c{k + 1}", "ReadGauge", {"station": "敖江", "attempt": k + 1})))
        rounds.append(_answer_round("已恢复，当前水位 12.3 m。"))
        tool_results = {
            f"c{i}": _call_result(f"错误：第 {i} 次遥测超时", status="error") for i in range(1, k + 1)
        }
        tool_results[f"c{k + 1}"] = _call_result("恢复成功，水位 12.3 m")
        cases.append(_case(
            f"regression-pack-failures-{k}-then-recover",
            "regression",
            f"连续工具失败 {k} 次后恢复：同一工具连续 {k} 次失败（参数各异，避开 DOOM 检测），"
            f"错误结果全部回填，第 {k + 1} 次调用成功且不触发强制终止（阈值 5）。",
            "执行多步查询，失败要恢复。",
            rounds,
            {
                "model_calls": k + 2,
                "tool_calls": ["ReadGauge"] * (k + 1),
                "tool_statuses": ["error"] * k + ["completed"],
                "invalid_calls": k,
                "final_output_contains": ["已恢复"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS)},
            tool_results=tool_results,
        ))

    # D5 连续失败 5 次强制终止：参数各异避开 DOOM LOOP，走到连续失败阈值。
    cases.append(_case(
        "regression-pack-failures-5-terminate",
        "regression",
        "连续工具失败 5 次：达到 MAX_CONSECUTIVE_TOOL_FAILURES 阈值后强制终止，"
        "终态文案说明已连续失败次数。每次调用参数不同，确保由连续失败检测而非 DOOM 拦截。",
        "一直重试直到成功。",
        [
            _tool_round(_call(f"c{i}", "ReadGauge", {"station": "敖江", "attempt": i}))
            for i in range(1, 6)
        ],
        {
            "model_calls": 5,
            "tool_calls": ["ReadGauge"] * 5,
            "tool_statuses": ["error"] * 5,
            "invalid_calls": 5,
            "final_output_contains": ["已连续失败 5 次"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS)},
        tool_results={
            f"c{i}": _call_result("错误：测站无响应", status="error") for i in range(1, 6)
        },
    ))

    # D6 失败计数按工具重置：失败-成功-失败-成功不终止。
    cases.append(_case(
        "regression-pack-failure-counter-resets",
        "regression",
        "失败计数重置：同一工具失败-成功-失败-成功交替（参数各异避开 DOOM 检测），"
        "成功把计数清零，从不达到连续失败阈值，循环正常结束。",
        "主备源交替查询。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "敖江", "attempt": 1})),
            _tool_round(_call("c2", "QueryLink", {"station": "敖江", "attempt": 2})),
            _tool_round(_call("c3", "QueryLink", {"station": "敖江", "attempt": 3})),
            _tool_round(_call("c4", "QueryLink", {"station": "敖江", "attempt": 4})),
            _answer_round("交替查询完成。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["QueryLink"] * 4,
            "tool_statuses": ["error", "completed", "error", "completed"],
            "invalid_calls": 2,
            "final_output_contains": ["完成"],
        },
        tools={"QueryLink": _tool_spec("主备源水位查询。", "占位", parameters=_GAUGE_PARAMS)},
        tool_results={
            "c1": _call_result("错误：链路超时", status="error"),
            "c2": _call_result("备源正常"),
            "c3": _call_result("错误：链路超时", status="error"),
            "c4": _call_result("备源正常"),
        },
    ))

    # D7 失败计数按工具名隔离：两个工具各失败 2 次不终止。
    cases.append(_case(
        "regression-pack-failures-isolated-per-tool",
        "regression",
        "失败计数隔离：QueryLink 与 QueryFlow 各失败 2 次，计数按工具名独立计算，"
        "均不达到阈值 5，循环正常结束。",
        "两条链路并行排查。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryFlow", {"station": "敖江"})),
            _tool_round(_call("c3", "QueryLink", {"station": "敖江"})),
            _tool_round(_call("c4", "QueryFlow", {"station": "敖江"})),
            _answer_round("两条链路均不可达，已记录。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["QueryLink", "QueryFlow", "QueryLink", "QueryFlow"],
            "tool_statuses": ["error", "error", "error", "error"],
            "invalid_calls": 4,
            "final_output_contains": ["已记录"],
        },
        tools={
            "QueryLink": _tool_spec("链路一查询。", "占位", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("链路二查询。", "占位", parameters=_GAUGE_PARAMS),
        },
        tool_results={
            "c1": _call_result("错误：链路一超时", status="error"),
            "c2": _call_result("错误：链路二超时", status="error"),
            "c3": _call_result("错误：链路一超时", status="error"),
            "c4": _call_result("错误：链路二超时", status="error"),
        },
    ))

    # E. 非法工具参数多种形态。
    invalid_shapes = [
        ("non-json", "station=敖江", "Expecting value: line 1 column 1 (char 0)"),
        ("wrong-type", '{"station": 42}', "42 is not of type 'string'"),
        ("empty-args", "", "arguments is empty"),
        ("trailing-garbage", '{"station": "敖江"} trailing', "Extra data: line 1 column 22"),
    ]
    for shape, raw, error in invalid_shapes:
        cases.append(_case(
            f"regression-pack-invalid-{shape}",
            "regression",
            f"非法工具参数（{shape}）：{error} 的结构化反馈回填模型上下文，工具未执行，"
            f"循环在下一轮给出解释性答复。",
            "查询敖江站水位。",
            [
                {"events": [_invalid("bad1", "ReadGauge", raw, error), _done("tool_calls")]},
                _answer_round(f"参数有误（{shape}），已放弃本次查询。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": [],
                "final_output_contains": ["参数有误"],
                "prompt_contains": [
                    {"call_index": 1, "contains": "参数 JSON 无法解析"},
                    {"call_index": 1, "contains": error},
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        ))

    # E5 一轮两个非法调用：两条反馈都进入上下文。
    cases.append(_case(
        "regression-pack-invalid-two-in-one-round",
        "regression",
        "一轮两个非法调用：同一轮出现两个 invalid_tool_call，两条结构化反馈都回填上下文，"
        "只消耗一次迭代。",
        "同时查询两个测站。",
        [
            {"events": [
                _invalid("bad1", "ReadGauge", "{}", "missing required field: station"),
                _invalid("bad2", "ReadGauge", "???", "Expecting value: line 1 column 1 (char 0)"),
                _done("tool_calls"),
            ]},
            _answer_round("两个调用参数都有误。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_contains": ["都有误"],
            "prompt_contains": [
                {"call_index": 1, "contains": "missing required field: station"},
                {"call_index": 1, "contains": "Expecting value"},
            ],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # E6 非法-合法-非法交替：中间合法调用正常执行。
    cases.append(_case(
        "regression-pack-invalid-valid-alternating",
        "regression",
        "非法-合法-非法交替：中间一轮的合法工具调用正常执行，第二次非法反馈同样回填。",
        "查询水位，中间穿插一次坏调用。",
        [
            {"events": [_invalid("bad1", "ReadGauge", "not-json", "Expecting value: line 1 column 1 (char 0)"), _done("tool_calls")]},
            _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
            {"events": [_invalid("bad2", "ReadGauge", '{"station": ', "Unexpected end of JSON input"), _done("tool_calls")]},
            _answer_round("拿到了一次有效水位。"),
        ],
        {
            "model_calls": 4,
            "tool_calls": ["ReadGauge"],
            "tool_statuses": ["completed"],
            "final_output_contains": ["有效水位"],
            "prompt_contains": [
                {"call_index": 3, "contains": "参数 JSON 无法解析"},
            ],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F. 终态家族：refusal / content_filter / pause_turn / abort。
    terminal_labels = {
        "refusal": ("refusal", "模型拒绝了该请求。"),
        "content-filter": ("content_filter", "模型输出被内容过滤器截断。"),
        "pause-turn": ("pause_turn", "模型暂停了当前回合，未正常完成。"),
        "abort-reason": ("abort", "模型调用已中止。"),
    }
    for name, (reason, label) in terminal_labels.items():
        cases.append(_case(
            f"regression-pack-terminal-{name}",
            "regression",
            f"终态 {reason}：执行器按契约映射为 failed 终态，最终答案为固定文案“{label}”。",
            "回答一个问题。",
            [{"events": [_done(reason)]}],
            {
                "model_calls": 1,
                "tool_calls": [],
                "final_output_equals": label,
            },
        ))

    # G. max_tokens 续写。
    cases.append(_case(
        "regression-pack-max-tokens-continuation",
        "regression",
        "max_tokens 续写成功：第一轮 length 触发续写，部分答案保留，续写指令进入上下文，"
        "第二轮答案拼接为完整结果。",
        "给出完整分析。",
        [
            {"events": [_token("前半段答案。"), _done("length")]},
            _answer_round("后半段答案。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_equals": "前半段答案。后半段答案。",
            "prompt_contains": [{"call_index": 1, "contains": "从中断处继续"}],
        },
    ))
    cases.append(_case(
        "regression-pack-max-tokens-exhausted",
        "regression",
        "max_tokens 续写耗尽：max_token_continuations=1，第二次 length 不再续写，"
        "两段部分输出拼接后 failed。",
        "给出完整分析。",
        [
            {"events": [_token("前半段答案。"), _done("length")]},
            {"events": [_token("仍然没说完。"), _done("max_tokens")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_equals": "前半段答案。仍然没说完。",
        },
    ))

    # H. DOOM LOOP（同工具同参数 3 次）与反向对照。
    cases.append(_case(
        "regression-pack-doom-loop-same-args",
        "regression",
        "DOOM LOOP：同一工具以完全相同参数连续调用 4 次，第 4 次在执行前被检测拦截，"
        "终态文案说明已连续 3 次相同参数调用。",
        "反复查询直到有结果。",
        [_tool_round(_call(f"c{i}", "ReadGauge", {"station": "敖江"})) for i in range(1, 5)],
        {
            "model_calls": 4,
            "tool_calls": ["ReadGauge"] * 3,
            "final_output_contains": ["已连续 3 次使用相同参数", "疑似死循环", "强制终止"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))
    cases.append(_case(
        "regression-pack-doom-loop-diff-args-control",
        "regression",
        "DOOM LOOP 反向对照：同一工具但每次参数不同（签名不同），连续 4 轮不触发死循环，"
        "循环正常 completed。",
        "遍历四个测站查询。",
        [
            *(_tool_round(_call(f"c{i}", "ReadGauge", {"station": f"站点{i}"})) for i in range(1, 5)),
            _answer_round("四个测站查询完毕。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["ReadGauge"] * 4,
            "tool_statuses": ["completed"] * 4,
            "final_output_contains": ["查询完毕"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # I. 未知工具（真实工具层）。
    cases.append(_case(
        "regression-pack-unknown-tool",
        "regression",
        "未知工具：真实工具执行器对未注册工具名 fail-closed 返回“未知工具”错误，"
        "循环继续并给出解释。",
        "调用一个不存在的工具。",
        [
            _tool_round(_call("c1", "NoSuchTool", {"file_path": "data.md"})),
            _answer_round("没有名为 NoSuchTool 的工具。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["NoSuchTool"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "未知工具: NoSuchTool"}],
            "final_output_contains": ["NoSuchTool"],
        },
        tool_mode="real",
        workspace_files={"data.md": "内容 42\n"},
        tools={"Read": {}},
    ))

    # J. 多工具部分失败。
    cases.append(_case(
        "regression-pack-multi-tool-partial-failure",
        "regression",
        "多工具部分失败：同轮两个工具一个失败一个成功，两者结果都按序回填上下文。",
        "同时查两条链路。",
        [
            _tool_round(
                _call("c1", "QueryLink", {"station": "敖江"}),
                _call("c2", "QueryBackup", {"station": "敖江"}),
            ),
            _answer_round("主链路失败，备用链路可用。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["QueryLink", "QueryBackup"],
            "tool_statuses": ["error", "completed"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "备源可用"}],
            "final_output_contains": ["备用链路可用"],
        },
        tools={
            "QueryLink": _tool_spec("主源查询。", "错误：主源超时", status="error", parameters=_GAUGE_PARAMS),
            "QueryBackup": _tool_spec("备用源查询。", "备源可用", parameters=_GAUGE_PARAMS),
        },
    ))

    # K. 预算检查先于迭代上界。
    cases.append(_case(
        "regression-pack-budget-check-before-max-iterations",
        "regression",
        "预算优先于迭代上界：max_iterations=1 且第一轮 usage 即超预算，"
        "终态为预算截断文案而非迭代兜底文案。",
        "查询并继续。",
        [
            {"events": [_usage(40, 20, 60), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 1,
            "tool_calls": ["ReadGauge"],
            "final_output_contains": ["预算上限 50", "累计 token 用量 60"],
            "final_output_not_contains": ["最大迭代"],
        },
        tools=budget_tools,
        max_iterations=1,
        max_total_tokens=50,
    ))

    # L. 中断与 usage 组合：第一轮用量正常累计，第二轮流式后被中断。
    cases.append(_case(
        "regression-pack-abort-with-usage",
        "regression",
        "中断与 usage 组合：两轮各带 usage 事件，abort 在第二次模型调用后生效，"
        "工具只执行一次，半截轮丢弃。",
        "开始一个较长任务。",
        [
            {"events": [_usage(20, 10, 30), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": list(_UNREACHED)},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "final_output_contains": ["中断"],
        },
        tools=budget_tools,
        abort_after_model_calls=2,
    ))

    # M. max_iterations 与多工具：每轮两个工具，截断时回退最后一条工具结果。
    cases.append(_case(
        "regression-pack-max-iterations-multi-tool",
        "regression",
        "迭代上界+多工具：max_iterations=2，每轮并行两个工具，截断时回退到最后一轮"
        "最后一条工具结果原文。",
        "持续并行查询。",
        [
            _tool_round(
                _call("c1", "QueryLevel", {"station": "敖江"}),
                _call("c2", "QueryFlow", {"station": "敖江"}),
            ),
            _tool_round(
                _call("c3", "QueryLevel", {"station": "敖江"}),
                _call("c4", "QueryFlow", {"station": "敖江"}),
            ),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryLevel", "QueryFlow"],
            "tool_statuses": ["completed"] * 4,
            "final_output_contains": ["第 2 轮流量 820 m³/s"],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "占位", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "占位", parameters=_GAUGE_PARAMS),
        },
        tool_results={
            "c1": _call_result("第 1 轮水位 12.3 m"),
            "c2": _call_result("第 1 轮流量 800 m³/s"),
            "c3": _call_result("第 2 轮水位 12.4 m"),
            "c4": _call_result("第 2 轮流量 820 m³/s"),
        },
        max_iterations=2,
    ))

    return cases


# ── regression extended pack：参数化家族扩容（C1 硬指标） ─────
#
# 家族命名 regression-ext-<family>[-<param>]；所有多轮同工具家族的每次调用参数
# 必须不同（DOOM LOOP 在同参数连续 3 次时于执行前拦截），同名工具多轮不同结果
# 一律用 tool_results 按调用 id 声明。每个家族断言各自的契约点，见各 case 的
# 中文 description。


def _regression_extended_pack() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    gauge = {"ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS)}
    flow = {"QueryFlow": _tool_spec("查询流量。", "占位", parameters=_GAUGE_PARAMS)}

    def _station_calls(prefix: str, tool: str, k: int) -> tuple:
        """生成 k 轮同工具异参调用：返回 (rounds, tool_results, contents)。"""
        rounds, results, contents = [], {}, []
        for i in range(1, k + 1):
            rounds.append(_tool_round(_call(f"{prefix}{i}", tool, {"station": f"站点{i}"})))
            content = f"站点{i} 水位 {i}.0 m"
            results[f"{prefix}{i}"] = _call_result(content)
            contents.append(content)
        return rounds, results, contents

    # F1 深链回填：单工具 k 轮循环，最后一轮上下文必须包含此前全部结果。
    for k in range(2, 13):
        rounds, results, contents = _station_calls("c", "ReadGauge", k)
        rounds.append(_answer_round("深链查询完毕。"))
        cases.append(_case(
            f"regression-ext-deep-chain-{k}",
            "regression",
            f"深链回填（深度 {k}）：单工具连续 {k} 轮异参调用，第 {k} 轮上下文必须同时"
            f"包含第 1 轮与第 {k - 1} 轮的结果，验证跨轮回填链在深度 {k} 处不丢结果。",
            "遍历各测站查询。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": ["ReadGauge"] * k,
                "tool_statuses": ["completed"] * k,
                "prompt_contains": [
                    {"call_index": k - 1, "contains": contents[0]},
                    {"call_index": k - 1, "contains": contents[-1]},
                ],
                "final_output_contains": ["深链查询完毕"],
            },
            tools=gauge,
            tool_results=results,
            max_iterations=k + 1,
        ))

    # F1b 第二工具家族深链：QueryFlow 深度 2..6，验证回填链与工具族无关。
    for k in range(2, 7):
        rounds, results, _ = _station_calls("f", "QueryFlow", k)
        rounds.append(_answer_round("流量链查询完毕。"))
        cases.append(_case(
            f"regression-ext-deep-chain-flow-{k}",
            "regression",
            f"深链回填·流量族（深度 {k}）：换用 QueryFlow 后回填链同样成立，"
            f"验证深链契约不绑定特定工具族。",
            "遍历各测站查流量。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": ["QueryFlow"] * k,
                "prompt_contains": [{"call_index": k - 1, "contains": "站点1 水位 1.0 m"}],
                "final_output_contains": ["流量链查询完毕"],
            },
            tools=flow,
            tool_results=results,
        ))

    # F2 双工具交替 k 轮：交错签名不触发 DOOM LOOP，结果交错回填。
    for k in range(2, 13):
        rounds, results = [], {}
        seq = []
        for i in range(1, k + 1):
            rounds.append(_tool_round(
                _call(f"a{i}", "QueryLevel", {"station": f"站点{i}"}),
                _call(f"b{i}", "QueryFlow", {"station": f"站点{i}"}),
            ))
            results[f"a{i}"] = _call_result(f"第{i}轮水位 {i}.0 m")
            results[f"b{i}"] = _call_result(f"第{i}轮流量 {i}0 m³/s")
            seq += ["QueryLevel", "QueryFlow"]
        rounds.append(_answer_round("交替查询完毕。"))
        cases.append(_case(
            f"regression-ext-alternating-{k}",
            "regression",
            f"双工具交替（{k} 轮）：QueryLevel/QueryFlow 每轮交错调用，连续签名永不同，"
            f"不触发死循环检测，且第 {k} 轮上下文同时含两工具上一轮结果。",
            "交替查询水位流量。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": seq,
                "tool_statuses": ["completed"] * (2 * k),
                "prompt_contains": [{"call_index": k, "contains": f"第{k}轮流量 {k}0 m³/s"}],
                "final_output_contains": ["交替查询完毕"],
            },
            tools={
                "QueryLevel": _tool_spec("查询水位。", "占位", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "占位", parameters=_GAUGE_PARAMS),
            },
            tool_results=results,
            max_iterations=k + 1,
        ))

    # F3 每轮双工具 k 轮（2k 次调用）：宽度×深度组合下的调度与回填。
    for k in range(2, 11):
        rounds, results = [], {}
        for i in range(1, k + 1):
            rounds.append(_tool_round(
                _call(f"c{i}", "ReadGauge", {"station": f"站点{i}"}),
                _call(f"d{i}", "QueryFlow", {"station": f"站点{i}"}),
            ))
            results[f"c{i}"] = _call_result(f"深{i}水位 {i}.0 m")
            results[f"d{i}"] = _call_result(f"深{i}流量 {i}0 m³/s")
        rounds.append(_answer_round("宽度深度查询完毕。"))
        cases.append(_case(
            f"regression-ext-two-tools-per-round-{k}",
            "regression",
            f"宽度×深度（{k} 轮×2 工具）：共 {2 * k} 次调用按序执行，第 {k} 轮上下文"
            f"仍包含第 1 轮结果，验证宽轮次循环下的顺序与回填。",
            "并行遍历查询。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": ["ReadGauge", "QueryFlow"] * k,
                "prompt_contains": [{"call_index": k, "contains": "深1水位 1.0 m"}],
                "final_output_contains": ["宽度深度查询完毕"],
            },
            tools={
                "ReadGauge": _tool_spec("读取测站水位。", "占位", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "占位", parameters=_GAUGE_PARAMS),
            },
            tool_results=results,
            max_iterations=k + 1,
        ))

    # F4 预算截断位置扫描：每轮 usage 20，预算=20k，恰在第 k 轮工具后截断。
    for k in range(1, 11):
        rounds = [
            {"events": [_usage(15, 5, 20), _call(f"c{i}", "ReadGauge",
                {"station": f"站点{i}"}), _done("tool_calls")]}
            for i in range(1, k + 1)
        ]
        cases.append(_case(
            f"regression-ext-budget-cut-after-round-{k}",
            "regression",
            f"预算截断位置（第 {k} 轮）：每轮 usage 20、预算 {20 * k}，累计恰在第 {k} 轮"
            f"工具执行后达到上限，第 {k + 1} 次模型调用不再发起，文案给出精确数字。",
            "持续查询。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["ReadGauge"] * k,
                "final_output_contains": [
                    f"预算上限 {20 * k}",
                    f"累计 token 用量 {20 * k}",
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_total_tokens=20 * k,
        ))

    # F5 预算粒度扫描：每轮 usage 30，预算=30k，验证不同粒度下的预算算术。
    for k in range(1, 9):
        rounds = [
            {"events": [_usage(20, 10, 30), _call(f"c{i}", "ReadGauge",
                {"station": f"站点{i}"}), _done("tool_calls")]}
            for i in range(1, k + 1)
        ]
        cases.append(_case(
            f"regression-ext-budget-granularity-30-{k}",
            "regression",
            f"预算粒度 30（第 {k} 轮截断）：每轮 usage 30、预算 {30 * k}，"
            f"验证非 20 粒度下的累计截断算术与文案数字。",
            "持续查询。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["ReadGauge"] * k,
                "final_output_contains": [
                    f"预算上限 {30 * k}",
                    f"累计 token 用量 {30 * k}",
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_total_tokens=30 * k,
        ))

    # F6 预算 +1 边界：预算=20k+1 时第 k 轮不截断，第 k+1 轮才触发。
    for k in range(1, 4):
        budget = 20 * k + 1
        rounds = [
            {"events": [_usage(15, 5, 20), _call(f"c{i}", "ReadGauge",
                {"station": f"站点{i}"}), _done("tool_calls")]}
            for i in range(1, k + 2)
        ]
        cases.append(_case(
            f"regression-ext-budget-over-by-one-{k}",
            "regression",
            f"预算 +1 边界（k={k}）：预算 {budget}，前 {k} 轮累计 {20 * k} 未触顶继续执行，"
            f"第 {k + 1} 轮累计 {20 * (k + 1)} 才截断——验证比较是“达到上限”而非“超过上限”。",
            "持续查询。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": ["ReadGauge"] * (k + 1),
                "final_output_contains": [
                    f"预算上限 {budget}",
                    f"累计 token 用量 {20 * (k + 1)}",
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_total_tokens=budget,
        ))

    # F7 预算 -1 边界：总用量永远差 1 触顶，循环必须正常 completed。
    for k in range(1, 4):
        budget = 20 * k + 11
        rounds = [
            {"events": [_usage(15, 5, 20), _call(f"c{i}", "ReadGauge",
                {"station": f"站点{i}"}), _done("tool_calls")]}
            for i in range(1, k + 1)
        ]
        rounds.append({"events": [_usage(8, 2, 10), _token("差一边界完成。"), _done("stop")]})
        cases.append(_case(
            f"regression-ext-budget-under-by-one-{k}",
            "regression",
            f"预算 -1 边界（k={k}）：工具轮累计 {20 * k}、答案轮 10，总用量 {20 * k + 10}"
            f"比预算 {budget} 差 1，全程不得截断，终态 completed。",
            "查询后总结。",
            rounds,
            {
                "model_calls": k + 1,
                "tool_calls": ["ReadGauge"] * k,
                "final_output_equals": "差一边界完成。",
                "final_output_not_contains": ["预算上限"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_total_tokens=budget,
        ))

    # F8 预算恰在答案轮达到：答案轮照常 completed（预算检查在发起下一轮之前）。
    cases.append(_case(
        "regression-ext-budget-exact-answer-round-completes",
        "regression",
        "预算答案轮边界：工具轮 20 + 答案轮 10 恰达预算 30，答案轮正常完成，"
        "不因达到预算而被截断——验证检查点在每轮发起前而非每轮结束后。",
        "查询后给出结论。",
        [
            {"events": [_usage(15, 5, 20), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            {"events": [_usage(8, 2, 10), _token("结论已给出。"), _done("stop")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "final_output_equals": "结论已给出。",
            "final_output_not_contains": ["预算上限"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        max_total_tokens=30,
    ))

    # F9 非法参数轮也消耗计费：invalid 轮带 usage，累计照样计入预算。
    cases.append(_case(
        "regression-ext-budget-invalid-round-counts",
        "regression",
        "预算与非法轮：invalid_tool_call 轮同样上报 usage 20，两轮累计 40 达预算截断，"
        "且非法反馈进入第二轮上下文——验证非法轮消耗计费额度与迭代。",
        "查询并继续。",
        [
            {"events": [
                _usage(15, 5, 20),
                _invalid("bad1", "ReadGauge", "not-json", "Expecting value: line 1 column 1 (char 0)"),
                _done("tool_calls"),
            ]},
            {"events": [_usage(15, 5, 20), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "prompt_contains": [{"call_index": 1, "contains": "参数 JSON 无法解析"}],
            "final_output_contains": ["预算上限 40", "累计 token 用量 40"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        max_total_tokens=40,
    ))

    # F10 预算 0 = 不限：多轮大额 usage 照常 completed。
    cases.append(_case(
        "regression-ext-budget-zero-unlimited",
        "regression",
        "预算零语义：max_total_tokens=0 表示不限制，三轮各 100 token 累计 300 照常完成。",
        "连续查询三轮。",
        [
            {"events": [_usage(70, 30, 100), _call("c1", "ReadGauge", {"station": "站点1"}), _done("tool_calls")]},
            {"events": [_usage(70, 30, 100), _call("c2", "ReadGauge", {"station": "站点2"}), _done("tool_calls")]},
            {"events": [_usage(70, 30, 100), _token("三轮查询完成。"), _done("stop")]},
        ],
        {
            "model_calls": 3,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "final_output_equals": "三轮查询完成。",
            "final_output_not_contains": ["预算上限"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        max_total_tokens=0,
    ))

    # F11 仅 total_tokens 字段：缺 prompt/completion 时预算仍按 total 截断。
    cases.append(_case(
        "regression-ext-budget-total-only",
        "regression",
        "usage 形状边界：usage 事件只给 total_tokens=50 时预算按 total 字段截断，"
        "缺省字段按 0 处理。",
        "查询并继续。",
        [
            {"events": [
                {"type": "usage", "usage": {"total_tokens": 50}},
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _done("tool_calls"),
            ]},
        ],
        {
            "model_calls": 1,
            "tool_calls": ["ReadGauge"],
            "final_output_contains": ["预算上限 50", "累计 token 用量 50"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        max_total_tokens=50,
    ))

    # F12 中断×多工具轮：abort 粒度是模型调用轮，轮内工具全部执行后才丢弃。
    for k in range(2, 11):
        rounds = [
            _tool_round(
                _call(f"a{i}", "QueryLevel", {"station": f"站点{i}"}),
                _call(f"b{i}", "QueryFlow", {"station": f"站点{i}"}),
            )
            for i in range(1, k)
        ]
        rounds.append({"events": list(_UNREACHED)})
        cases.append(_case(
            f"regression-ext-abort-multi-tool-{k}",
            "regression",
            f"中断×多工具（第 {k} 次模型调用后）：前 {k - 1} 轮每轮两个工具全部执行，"
            f"第 {k} 轮整轮丢弃——中断粒度是模型调用轮而非单个工具。",
            "开始并行任务。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["QueryLevel", "QueryFlow"] * (k - 1),
                "final_output_contains": ["中断"],
            },
            tools={
                "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
            },
            abort_after_model_calls=k,
        ))

    # F13 中断永不触发：abort_after 大于脚本轮数时正常 completed。
    cases.append(_case(
        "regression-ext-abort-never-fires",
        "regression",
        "中断未触发：abort_after_model_calls=10 而脚本只有 2 轮，循环正常 completed，"
        "工具照常执行。",
        "简单查询。",
        [
            _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
            _answer_round("查询完成。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "tool_statuses": ["completed"],
            "final_output_equals": "查询完成。",
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        abort_after_model_calls=10,
    ))

    # F14 中断与 usage 共存（第 3..5 调用点）：中断语义不受计费事件影响。
    for k in range(3, 6):
        rounds = [
            {"events": [_usage(10, 5, 15), _call(f"c{i}", "ReadGauge",
                {"station": f"站点{i}"}), _done("tool_calls")]}
            for i in range(1, k)
        ]
        rounds.append({"events": [_usage(10, 5, 15), _token("不应到达这一轮。"), _done("stop")]})
        cases.append(_case(
            f"regression-ext-abort-with-usage-{k}",
            "regression",
            f"中断×usage（第 {k} 次模型调用后）：每轮带 usage 事件，abort 仍在第 {k} 次"
            f"模型调用后生效，前 {k - 1} 轮工具执行、第 k 轮丢弃——计费事件不改变中断语义。",
            "开始一个较长任务。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["ReadGauge"] * (k - 1),
                "final_output_contains": ["中断"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            abort_after_model_calls=k,
        ))

    # F15 迭代上界×多工具：截断时回退最后一轮最后一条工具结果。
    for k in range(2, 6):
        rounds, results = [], {}
        for i in range(1, k + 1):
            rounds.append(_tool_round(
                _call(f"a{i}", "QueryLevel", {"station": f"站点{i}"}),
                _call(f"b{i}", "QueryFlow", {"station": f"站点{i}"}),
            ))
            results[f"a{i}"] = _call_result(f"轮{i}水位 {i}.0 m")
            results[f"b{i}"] = _call_result(f"轮{i}流量 {i}0 m³/s")
        cases.append(_case(
            f"regression-ext-maxiter-multi-tool-{k}",
            "regression",
            f"迭代上界×多工具（max_iterations={k}）：共 {2 * k} 次调用后截断，"
            f"终态回退最后一轮最后一条工具结果“轮{k}流量 {k}0 m³/s”原文。",
            "持续并行查询。",
            rounds,
            {
                "model_calls": k,
                "tool_calls": ["QueryLevel", "QueryFlow"] * k,
                "tool_statuses": ["completed"] * (2 * k),
                "final_output_contains": [f"轮{k}流量 {k}0 m³/s"],
            },
            tools={
                "QueryLevel": _tool_spec("查询水位。", "占位", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "占位", parameters=_GAUGE_PARAMS),
            },
            tool_results=results,
            max_iterations=k,
        ))

    # F16 预算优先于迭代上界（k=2,3）：两者同时存在时预算文案胜出。
    for k in (2, 3):
        cases.append(_case(
            f"regression-ext-budget-beats-maxiter-{k}",
            "regression",
            f"预算优先于迭代上界（max_iterations={k}）：每轮 usage 20、预算 30，"
            f"第二轮后累计 40 达预算，终态为预算截断文案而非迭代兜底文案。",
            "查询并继续。",
            [
                {"events": [_usage(15, 5, 20), _call("c1", "ReadGauge", {"station": "站点1"}), _done("tool_calls")]},
                {"events": [_usage(15, 5, 20), _call("c2", "ReadGauge", {"station": "站点2"}), _done("tool_calls")]},
            ],
            {
                "model_calls": 2,
                "tool_calls": ["ReadGauge", "ReadGauge"],
                "final_output_contains": ["预算上限 30"],
                "final_output_not_contains": ["最大迭代"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_iterations=k,
            max_total_tokens=30,
        ))

    # F17 截断后不执行未到达的脚本轮：fallback 只含已执行轮的结果。
    cases.append(_case(
        "regression-ext-maxiter-truncates-script",
        "regression",
        "截断守卫：max_iterations=2 而脚本声明 4 轮，第 3、4 轮绝不被请求，"
        "终态只含第 2 轮结果、不含第 3 轮结果文本。",
        "不断查询。",
        [
            _tool_round(_call("c1", "ReadGauge", {"station": "站点1", "attempt": 1})),
            _tool_round(_call("c2", "ReadGauge", {"station": "站点2", "attempt": 2})),
            _tool_round(_call("c3", "ReadGauge", {"station": "站点3", "attempt": 3})),
            _tool_round(_call("c4", "ReadGauge", {"station": "站点4", "attempt": 4})),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "final_output_contains": ["第 2 轮水位 2.0 m"],
            "final_output_not_contains": ["第 3 轮水位 3.0 m"],
        },
        tools=gauge,
        tool_results={
            "c1": _call_result("第 1 轮水位 1.0 m"),
            "c2": _call_result("第 2 轮水位 2.0 m"),
            "c3": _call_result("第 3 轮水位 3.0 m"),
            "c4": _call_result("第 4 轮水位 4.0 m"),
        },
        max_iterations=2,
    ))

    # F18 终态映射别名表：不同 wire 值映射到同一契约标签。
    alias_cases = [
        ("end-turn", [{"events": [_token("直接答案。"), _done("end_turn")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "直接答案。"}, "end_turn 映射为 completed"),
        ("tool-use", None, None, "tool_use 映射为 tool_calls"),
        ("filtered-alias", [{"events": [_done("filtered")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型输出被内容过滤器截断。"}, "filtered 别名"),
        ("refused-alias", [{"events": [_done("refused")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型拒绝了该请求。"}, "refused 别名"),
        ("paused-alias", [{"events": [_done("paused")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型暂停了当前回合，未正常完成。"}, "paused 别名"),
        ("aborted-alias", [{"events": [_done("aborted")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型调用已中止。"}, "aborted 别名"),
        ("error-reason", [{"events": [_done("error")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型回合异常结束。"}, "error 终态（非事件）固定文案"),
        ("unknown-reason", [{"events": [_done("mystery_reason")]}],
         {"model_calls": 1, "tool_calls": [],
          "final_output_equals": "模型以未知原因结束: mystery_reason"}, "未知终态原样回显 wire 值"),
    ]
    for name, rnds, exp, contract in alias_cases:
        if name == "tool-use":
            rnds = [
                _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
                _answer_round("别名终态下工具照常执行。"),
            ]
            exp = {
                "model_calls": 2,
                "tool_calls": ["ReadGauge"],
                "tool_statuses": ["completed"],
                "final_output_contains": ["照常执行"],
            }
        cases.append(_case(
            f"regression-ext-terminal-alias-{name}",
            "regression",
            f"终态别名（{contract}）：验证 wire 值到契约标签/行为的确定性映射。",
            "回答或调用工具。",
            rnds,
            exp,
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        ))

    # F19 超时事件：TimeoutError 路径，is_timeout=True，文案含失败原因。
    cases.append(_case(
        "regression-ext-timeout-event",
        "regression",
        "超时事件：模型流抛出超时，终态 failed、文案为“模型调用失败: …超时…”，"
        "is_timeout 标志置位。",
        "回答一个问题。",
        [{"events": [{"type": "timeout", "content": "模型响应超时，请稍后重试"}]}],
        {
            "model_calls": 1,
            "tool_calls": [],
            "final_output_contains": ["模型调用失败: 模型响应超时"],
            "is_timeout": True,
        },
    ))

    # F20 错误事件：不同错误文案确定性进入最终答案。
    for idx, msg in enumerate(["模拟服务商 500 内部错误", "连接被重置"], start=1):
        cases.append(_case(
            f"regression-ext-error-event-{idx}",
            "regression",
            f"错误事件（{msg}）：error 事件不重试，失败文案精确包含该错误信息。",
            "回答一个问题。",
            [{"events": [{"type": "error", "content": msg}]}],
            {
                "model_calls": 1,
                "tool_calls": [],
                "final_output_equals": f"模型调用失败: {msg}",
            },
        ))

    # F21 软错误内容也计入连续失败：status=completed 但内容以“错误”开头，
    # 连续 5 次同样强制终止。
    soft_rounds = [
        _tool_round(_call(f"c{i}", "ReadGauge", {"station": f"站点{i}"}))
        for i in range(1, 6)
    ]
    cases.append(_case(
        "regression-ext-soft-error-five-terminate",
        "regression",
        "软错误失败计数：工具结果 status=completed 但内容前缀为“错误：”时照样累计"
        "连续失败，第 5 次强制终止——验证计数依据内容而非仅状态。",
        "一直重试。",
        soft_rounds,
        {
            "model_calls": 5,
            "tool_calls": ["ReadGauge"] * 5,
            "tool_statuses": ["completed"] * 5,
            "final_output_contains": ["已连续失败 5 次"],
        },
        tools=gauge,
        tool_results={
            f"c{i}": _call_result(f"错误：软错误 {i}") for i in range(1, 6)
        },
    ))

    # F22 软错误后成功同样清零计数。
    cases.append(_case(
        "regression-ext-soft-error-resets",
        "regression",
        "软错误计数重置：软错误-成功-软错误-成功交替，成功轮把计数清零，"
        "循环正常结束。",
        "主备源交替查询。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "站点1"})),
            _tool_round(_call("c2", "QueryLink", {"station": "站点2"})),
            _tool_round(_call("c3", "QueryLink", {"station": "站点3"})),
            _tool_round(_call("c4", "QueryLink", {"station": "站点4"})),
            _answer_round("交替查询完成。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["QueryLink"] * 4,
            "tool_statuses": ["completed"] * 4,
            "final_output_contains": ["完成"],
        },
        tools={"QueryLink": _tool_spec("链路查询。", "占位", parameters=_GAUGE_PARAMS)},
        tool_results={
            "c1": _call_result("错误：软超时 1"),
            "c2": _call_result("备源正常"),
            "c3": _call_result("错误：软超时 3"),
            "c4": _call_result("备源正常"),
        },
    ))

    # F23 失败后换工具恢复（k=2..4；k=5 时第 5 次失败已触发强制终止，不可恢复）。
    for k in range(2, 5):
        rounds = [
            _tool_round(_call(f"c{i}", "QueryLink", {"station": f"站点{i}"}))
            for i in range(1, k + 1)
        ]
        rounds.append(_tool_round(_call(f"c{k + 1}", "QueryBackup", {"station": "汇总"})))
        rounds.append(_answer_round("切换备用链路成功。"))
        results = {
            f"c{i}": _call_result(f"错误：主链路失败 {i}", status="error")
            for i in range(1, k + 1)
        }
        results[f"c{k + 1}"] = _call_result("备用链路正常")
        cases.append(_case(
            f"regression-ext-fail-switch-tool-{k}",
            "regression",
            f"换工具恢复（主链路连失败 {k} 次）：QueryLink 连续失败 {k} 次后换 QueryBackup"
            f"一次成功，计数器按工具名隔离不跨工具继承，循环正常结束。",
            "主链路失败就换备用。",
            rounds,
            {
                "model_calls": k + 2,
                "tool_calls": ["QueryLink"] * k + ["QueryBackup"],
                "tool_statuses": ["error"] * k + ["completed"],
                "invalid_calls": k,
                "final_output_contains": ["切换备用链路成功"],
            },
            tools={
                "QueryLink": _tool_spec("主链路查询。", "占位", parameters=_GAUGE_PARAMS),
                "QueryBackup": _tool_spec("备用链路查询。", "备用链路正常", parameters=_GAUGE_PARAMS),
            },
            tool_results=results,
        ))

    # F24 非法参数形态扩展（8 种）：每种 malformed 形态的反馈文本原样回填。
    shapes24 = [
        ("array-root", "[1, 2, 3]", "JSON root must be an object"),
        ("string-root", "\"敖江\"", "JSON root must be an object"),
        ("number-root", "42", "JSON root must be an object"),
        ("bool-root", "true", "JSON root must be an object"),
        ("trailing-comma", "{\"station\": \"敖江\",}", "Expecting property name enclosed in double quotes"),
        ("unclosed-object", "{\"station\": \"敖江\", \"date\": ", "Unexpected end of JSON input"),
        ("bad-escape", "{\"station\": \"\\q\"}", "Invalid escape sequence"),
        ("single-quotes", "{'station': '敖江'}", "Expecting property name enclosed in double quotes"),
    ]
    for name, raw, error in shapes24:
        cases.append(_case(
            f"regression-ext-invalid-shape-{name}",
            "regression",
            f"非法参数形态（{name}）：{error} 的结构化反馈原样回填下一轮上下文，"
            f"工具未执行，循环给出解释性答复。",
            "查询敖江站水位。",
            [
                {"events": [_invalid("bad1", "ReadGauge", raw, error), _done("tool_calls")]},
                _answer_round(f"参数形态 {name} 有误，已放弃本次查询。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": [],
                "final_output_contains": ["参数形态"],
                "prompt_contains": [
                    {"call_index": 1, "contains": "参数 JSON 无法解析"},
                    {"call_index": 1, "contains": error},
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        ))

    # F49 单轮三个非法调用：三条反馈全部回填。
    cases.append(_case(
        "regression-ext-invalid-three-in-one-round",
        "regression",
        "一轮三非法：同一轮出现三个 invalid_tool_call（各不相同错误），三条反馈全部"
        "进入下一轮上下文，只消耗一次迭代。",
        "同时发起三个查询。",
        [
            {"events": [
                _invalid("bad1", "ReadGauge", "{}", "missing required field: station"),
                _invalid("bad2", "ReadGauge", "??", "Expecting value: line 1 column 1 (char 0)"),
                _invalid("bad3", "ReadGauge", "{\"station\": 7}", "7 is not of type 'string'"),
                _done("tool_calls"),
            ]},
            _answer_round("三个调用参数全部有误。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_contains": ["全部有误"],
            "prompt_contains": [
                {"call_index": 1, "contains": "missing required field: station"},
                {"call_index": 1, "contains": "Expecting value"},
                {"call_index": 1, "contains": "7 is not of type 'string'"},
            ],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F25 非法反馈不阻止终态判定：invalid 轮之后直接终态。
    for name, reason in (("refused", "refused"), ("filtered", "content_filter")):
        label = ("模型拒绝了该请求。" if reason == "refused"
                 else "模型输出被内容过滤器截断。")
        cases.append(_case(
            f"regression-ext-invalid-then-terminal-{name}",
            "regression",
            f"非法+终态组合：invalid 反馈进入历史后模型以 {reason} 终态结束，"
            f"终态标签判定不受历史非法调用影响。",
            "查询。",
            [
                {"events": [_invalid("bad1", "ReadGauge", "not-json", "Expecting value: line 1 column 1 (char 0)"), _done("tool_calls")]},
                {"events": [_done(reason)]},
            ],
            {
                "model_calls": 2,
                "tool_calls": [],
                "final_output_equals": label,
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        ))

    # F26 同轮非法+stop：invalid 使该轮按工具轮处理（消耗一次迭代并继续循环），
    # done(stop) 不生效；非法反馈进入下一轮上下文。
    cases.append(_case(
        "regression-ext-invalid-with-stop-same-round",
        "regression",
        "同轮非法+stop：invalid_tool_call 与 done(stop) 同轮出现时该轮按工具轮处理，"
        "stop 被忽略、迭代照常消耗，非法反馈进入下一轮上下文后才允许答案轮。",
        "直接作答。",
        [
            {"events": [
                _invalid("bad1", "ReadGauge", "not-json", "Expecting value: line 1 column 1 (char 0)"),
                _token("直接作答。"),
                _done("stop"),
            ]},
            _answer_round("上一轮参数有误，本轮作答。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_equals": "上一轮参数有误，本轮作答。",
            "prompt_contains": [{"call_index": 1, "contains": "参数 JSON 无法解析"}],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F27 空工具轮：done(tool_calls) 但无调用事件且只有文本，按答案轮处理直接结束。
    cases.append(_case(
        "regression-ext-empty-tool-round",
        "regression",
        "空工具轮：done(tool_calls) 而无任何 tool_call_done 事件时按无工具调用处理，"
        "同轮文本成为最终答案，循环不再推进。",
        "先占位再作答。",
        [{"events": [_token("准备查询。"), _done("tool_calls")]}],
        {
            "model_calls": 1,
            "tool_calls": [],
            "final_output_equals": "准备查询。",
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F28 工具轮带 reasoning + 答案轮带 reasoning：推理通道与答案通道互不污染。
    cases.append(_case(
        "regression-ext-reasoning-both-rounds",
        "regression",
        "双轮推理：工具轮与答案轮各带 reasoning 事件，最终答案只含答案轮 token，"
        "推理内容不混入。",
        "分析并查询。",
        [
            {"events": [
                _reasoning("推理一：先查水位。"),
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _done("tool_calls"),
            ]},
            {"events": [
                _reasoning("推理二：汇总。"),
                _token("最终结论：水位 12.3 m。"),
                _done("stop"),
            ]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "final_output_equals": "最终结论：水位 12.3 m。",
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F29 工具调用后同轮输出文本：文本进入助手消息并回填下一轮上下文。
    cases.append(_case(
        "regression-ext-token-after-tool-call",
        "regression",
        "工具后文本：tool_call_done 之后再输出 token，文本随助手消息进入下一轮上下文，"
        "最终答案仍只含答案轮内容。",
        "查询并说明。",
        [
            {"events": [
                _call("c1", "ReadGauge", {"station": "敖江"}),
                _token("已拿到原始读数。"),
                _done("tool_calls"),
            ]},
            _answer_round("读数确认：12.3 m。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge"],
            "final_output_equals": "读数确认：12.3 m。",
            "prompt_contains": [{"call_index": 1, "contains": "已拿到原始读数。"}],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F30 DOOM 滑动窗口：3 次同参后换参再换回，检测只看最近 3 次同工具签名。
    cases.append(_case(
        "regression-ext-doom-sliding-window",
        "regression",
        "DOOM 滑动窗口：同参×3（执行）→异参（执行）→再同参（执行），最近 3 次签名为"
        "“同、异、同”不构成死循环，5 次调用全部执行——验证窗口是滑动且异参打破连续性。",
        "反复查询同一站。",
        [
            _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
            _tool_round(_call("c2", "ReadGauge", {"station": "敖江"})),
            _tool_round(_call("c3", "ReadGauge", {"station": "敖江"})),
            _tool_round(_call("c4", "ReadGauge", {"station": "闽江"})),
            _tool_round(_call("c5", "ReadGauge", {"station": "敖江"})),
            _answer_round("滑动窗口验证完毕。"),
        ],
        {
            "model_calls": 6,
            "tool_calls": ["ReadGauge"] * 5,
            "tool_statuses": ["completed"] * 5,
            "final_output_contains": ["滑动窗口验证完毕"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
    ))

    # F31 DOOM 按连续签名而非累计：两工具交错各 3 次同参，不触发死循环。
    cases.append(_case(
        "regression-ext-doom-interleaved-tools",
        "regression",
        "DOOM 连续性：QueryLevel/QueryFlow 交错各以相同参数调用 3 次，同工具累计虽达 3"
        "次但从不连续，检测不触发，6 次调用全部执行。",
        "两链路并行轮询。",
        [
            _tool_round(
                _call(f"a{i}", "QueryLevel", {"station": "敖江"}),
                _call(f"b{i}", "QueryFlow", {"station": "敖江"}),
            )
            for i in range(1, 4)
        ] + [_answer_round("交错轮询完毕。")],
        {
            "model_calls": 4,
            "tool_calls": ["QueryLevel", "QueryFlow"] * 3,
            "tool_statuses": ["completed"] * 6,
            "final_output_contains": ["交错轮询完毕"],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
        },
    ))

    # F32 DOOM 按尝试计：同参 4 次但每次都失败，第 4 次被 DOOM 拦截而非失败终止。
    cases.append(_case(
        "regression-ext-doom-counts-attempts",
        "regression",
        "DOOM 计尝试不计结果：同参调用 4 次且结果全部报错，第 4 次在执行前被死循环检测"
        "拦截（终态文案为“死循环”而非“连续失败 5 次”）——验证 doom 先于失败计数生效。",
        "反复重试同一查询。",
        [_tool_round(_call(f"c{i}", "ReadGauge", {"station": "敖江"})) for i in range(1, 5)],
        {
            "model_calls": 4,
            "tool_calls": ["ReadGauge"] * 3,
            "tool_statuses": ["error"] * 3,
            "invalid_calls": 3,
            "final_output_contains": ["已连续 3 次使用相同参数"],
            "final_output_not_contains": ["已连续失败 5 次"],
        },
        tools=gauge,
        tool_results={
            f"c{i}": _call_result("错误：测站无响应", status="error") for i in range(1, 4)
        },
    ))

    # F33 多工具部分失败变体。
    cases.append(_case(
        "regression-ext-partial-failure-two-errors",
        "regression",
        "部分失败（2 错 1 对）：单轮三工具两个报错一个成功，三条结果按序回填，"
        "invalid_calls=2，循环继续完成。",
        "同时查三条链路。",
        [
            _tool_round(
                _call("c1", "QueryLink", {"station": "敖江"}),
                _call("c2", "QueryBackup", {"station": "敖江"}),
                _call("c3", "QueryRain", {"station": "敖江"}),
            ),
            _answer_round("只有雨量链路可用。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["QueryLink", "QueryBackup", "QueryRain"],
            "tool_statuses": ["error", "error", "completed"],
            "invalid_calls": 2,
            "prompt_contains": [{"call_index": 1, "contains": "雨量正常"}],
            "final_output_contains": ["只有雨量链路可用"],
        },
        tools={
            "QueryLink": _tool_spec("链路一。", "错误：超时", status="error", parameters=_GAUGE_PARAMS),
            "QueryBackup": _tool_spec("链路二。", "错误：超时", status="error", parameters=_GAUGE_PARAMS),
            "QueryRain": _tool_spec("雨量链路。", "雨量正常", parameters=_GAUGE_PARAMS),
        },
    ))
    cases.append(_case(
        "regression-ext-partial-failure-across-rounds",
        "regression",
        "跨轮部分失败：第一轮成功、第二轮报错，成功结果不回填第二轮之前，"
        "失败反馈按序进入第三轮上下文。",
        "先查可用链路再查故障链路。",
        [
            _tool_round(_call("c1", "QueryBackup", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryLink", {"station": "敖江"})),
            _answer_round("主链路故障已记录。"),
        ],
        {
            "model_calls": 3,
            "tool_calls": ["QueryBackup", "QueryLink"],
            "tool_statuses": ["completed", "error"],
            "invalid_calls": 1,
            "prompt_contains": [
                {"call_index": 1, "contains": "备源正常"},
                {"call_index": 2, "contains": "错误：主源超时"},
            ],
            "final_output_contains": ["已记录"],
        },
        tools={
            "QueryBackup": _tool_spec("备用链路。", "备源正常", parameters=_GAUGE_PARAMS),
            "QueryLink": _tool_spec("主链路。", "错误：主源超时", status="error", parameters=_GAUGE_PARAMS),
        },
    ))
    cases.append(_case(
        "regression-ext-partial-failure-success-first",
        "regression",
        "部分失败（先对后错）：单轮先成功后报错，与先错后成功对称，验证结果回填顺序"
        "与调用顺序一致。",
        "先查备用再查主链路。",
        [
            _tool_round(
                _call("c1", "QueryBackup", {"station": "敖江"}),
                _call("c2", "QueryLink", {"station": "敖江"}),
            ),
            _answer_round("备用可用，主链路故障。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["QueryBackup", "QueryLink"],
            "tool_statuses": ["completed", "error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "备源正常"}],
            "final_output_contains": ["主链路故障"],
        },
        tools={
            "QueryBackup": _tool_spec("备用链路。", "备源正常", parameters=_GAUGE_PARAMS),
            "QueryLink": _tool_spec("主链路。", "错误：主源超时", status="error", parameters=_GAUGE_PARAMS),
        },
    ))

    # F34 连续失败反馈可区分：3 次失败各自的消息按序出现在下一轮上下文。
    cases.append(_case(
        "regression-ext-failure-messages-backfilled",
        "regression",
        "失败反馈链：同一工具连失败 3 次（每次报错文本不同），第 4 轮上下文按序包含"
        "全部三条不同错误消息，随后恢复成功。",
        "重试直到恢复。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "站点1"})),
            _tool_round(_call("c2", "QueryLink", {"station": "站点2"})),
            _tool_round(_call("c3", "QueryLink", {"station": "站点3"})),
            _tool_round(_call("c4", "QueryLink", {"station": "站点4"})),
            _answer_round("链路已恢复。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["QueryLink"] * 4,
            "tool_statuses": ["error", "error", "error", "completed"],
            "invalid_calls": 3,
            "prompt_contains": [
                {"call_index": 4, "contains": "错误：第一次超时"},
                {"call_index": 4, "contains": "错误：第二次鉴权失败"},
                {"call_index": 4, "contains": "错误：第三次网关错误"},
            ],
            "final_output_contains": ["链路已恢复"],
        },
        tools={"QueryLink": _tool_spec("链路查询。", "占位", parameters=_GAUGE_PARAMS)},
        tool_results={
            "c1": _call_result("错误：第一次超时", status="error"),
            "c2": _call_result("错误：第二次鉴权失败", status="error"),
            "c3": _call_result("错误：第三次网关错误", status="error"),
            "c4": _call_result("链路恢复"),
        },
    ))

    # F35 非法+错误+成功混合序列（非 held-out 版，顺序为 错→非法→对）。
    cases.append(_case(
        "regression-ext-mixed-error-invalid-success",
        "regression",
        "混合序列：工具报错 → 非法参数 → 成功恢复，三类反馈共存于最终轮上下文，"
        "invalid_calls=1 只计真实执行失败的工具。",
        "排查并恢复查询。",
        [
            _tool_round(_call("c1", "QueryLink", {"station": "敖江"})),
            {"events": [_invalid("bad1", "ReadGauge", "{", "Unexpected end of JSON input"), _done("tool_calls")]},
            _tool_round(_call("c2", "QueryBackup", {"station": "敖江"})),
            _answer_round("已恢复：水位 12.3 m。"),
        ],
        {
            "model_calls": 4,
            "tool_calls": ["QueryLink", "QueryBackup"],
            "tool_statuses": ["error", "completed"],
            "invalid_calls": 1,
            "prompt_contains": [
                {"call_index": 3, "contains": "遥测链路超时"},
                {"call_index": 3, "contains": "参数 JSON 无法解析"},
                {"call_index": 3, "contains": "恢复后水位 12.3 m"},
            ],
            "final_output_contains": ["已恢复"],
        },
        tools={
            "QueryLink": _tool_spec("主源查询。", "占位", parameters=_GAUGE_PARAMS),
            "QueryBackup": _tool_spec("备用源查询。", "恢复后水位 12.3 m", parameters=_GAUGE_PARAMS),
            "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
        },
        tool_results={
            "c1": _call_result("错误：遥测链路超时", status="error"),
        },
    ))

    # F36 未知工具后接真实工具恢复：fail-closed 错误不杀死循环。
    cases.append(_case(
        "regression-ext-unknown-tool-then-real-read",
        "regression",
        "未知工具恢复：真实工具层对未注册工具 fail-closed 报错后，后续真实 Read 正常"
        "执行——验证未知工具错误是可恢复软失败。",
        "用不存在的工具读文件。",
        [
            _tool_round(_call("c1", "NoSuchTool", {"file_path": "data.md"})),
            _tool_round(_call("c2", "Read", {"file_path": "data.md"})),
            _answer_round("换用 Read 成功拿到内容。"),
        ],
        {
            "model_calls": 3,
            "tool_calls": ["NoSuchTool", "Read"],
            "tool_statuses": ["error", "completed"],
            "invalid_calls": 1,
            "prompt_contains": [
                {"call_index": 1, "contains": "未知工具: NoSuchTool"},
                {"call_index": 2, "contains": "内容 42"},
            ],
            "final_output_contains": ["成功拿到内容"],
        },
        tool_mode="real",
        workspace_files={"data.md": "内容 42\n"},
        tools={"Read": {}},
    ))

    # F37+ 真实工具回归子族：写读改生命周期、覆盖写、空文件、Unicode 文件名、
    # 嵌套检索、Grep 正则语义、缺失后自建再读、连读嵌套 fixture。
    real_cases = [
        (
            "read-missing-write-readback",
            "真实读-写-读：Read 缺失文件（软错误）→ Write 创建 → Read 回读，"
            "三步在真实工具层完成自愈闭环。",
            [
                _tool_round(_call("c1", "Read", {"file_path": "log.txt"})),
                _tool_round(_call("c2", "Write", {"file_path": "log.txt", "content": "首条记录\n"})),
                _tool_round(_call("c3", "Read", {"file_path": "log.txt"})),
                _answer_round("文件已创建并回读确认。"),
            ],
            {
                "model_calls": 4,
                "tool_calls": ["Read", "Write", "Read"],
                "tool_statuses": ["error", "completed", "completed"],
                "workspace_contains": {"log.txt": "首条记录"},
                "prompt_contains": [
                    {"call_index": 1, "contains": "文件不存在"},
                    {"call_index": 2, "contains": "文件写入成功"},
                    {"call_index": 3, "contains": "首条记录"},
                ],
                "final_output_contains": ["回读确认"],
            },
            None,
            {"Read": {}, "Write": {}},
        ),
        (
            "glob-grep-positive",
            "真实检索组合：Glob 找到 2 份文档，Grep 正则命中水位行，两者结果同轮回填。",
            [
                _tool_round(
                    _call("c1", "Glob", {"pattern": "**/*.md"}),
                    _call("c2", "Grep", {"pattern": "12\\.3"}),
                ),
                _answer_round("检索完成。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Glob", "Grep"],
                "tool_statuses": ["completed", "completed"],
                "prompt_contains": [
                    {"call_index": 1, "contains": "找到 2 个匹配文件"},
                    {"call_index": 1, "contains": "12.3"},
                ],
                "final_output_contains": ["检索完成"],
            },
            {"docs/x.md": "敖江 12.3 m\n", "docs/y.md": "闽江 8.1 m\n"},
            {"Glob": {}, "Grep": {}},
        ),
        (
            "write-overwrite",
            "真实覆盖写：默认模式第二次 Write 覆盖第一次内容，Read 回读只含新内容。",
            [
                _tool_round(_call("c1", "Write", {"file_path": "cfg.txt", "content": "version1\n"})),
                _tool_round(_call("c2", "Write", {"file_path": "cfg.txt", "content": "version2\n"})),
                _tool_round(_call("c3", "Read", {"file_path": "cfg.txt"})),
                _answer_round("配置已更新到 version2。"),
            ],
            {
                "model_calls": 4,
                "tool_calls": ["Write", "Write", "Read"],
                "tool_statuses": ["completed"] * 3,
                "workspace_contains": {"cfg.txt": "version2"},
                "prompt_contains": [{"call_index": 3, "contains": "version2"}],
                "final_output_contains": ["version2"],
            },
            None,
            {"Write": {}, "Read": {}},
        ),
        (
            "read-empty-file",
            "真实空文件：fixture 内容为空串，Read 正常 completed，模型据空内容作答。",
            [
                _tool_round(_call("c1", "Read", {"file_path": "empty.md"})),
                _answer_round("文件内容为空。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Read"],
                "tool_statuses": ["completed"],
                "final_output_contains": ["为空"],
            },
            {"empty.md": ""},
            {"Read": {}},
        ),
        (
            "unicode-filename",
            "真实 Unicode 文件名：中文加希腊字母的文件名写入后 Glob 枚举命中，"
            "内容无损。",
            [
                _tool_round(_call("c1", "Write", {"file_path": "数据/测站Ω.md", "content": "水位 12.3 m\n"})),
                _tool_round(_call("c2", "Glob", {"pattern": "数据/*.md"})),
                _answer_round("Unicode 文件名写入成功。"),
            ],
            {
                "model_calls": 3,
                "tool_calls": ["Write", "Glob"],
                "tool_statuses": ["completed", "completed"],
                "workspace_contains": {"数据/测站Ω.md": "水位 12.3 m"},
                "prompt_contains": [{"call_index": 2, "contains": "测站Ω.md"}],
                "final_output_contains": ["写入成功"],
            },
            None,
            {"Write": {}, "Glob": {}},
        ),
        (
            "edit-twice-lifecycle",
            "真实写改改读：Write 建文件后连续两次 Edit（先错字后数值），Read 回读最终态"
            "同时含两处修改。",
            [
                _tool_round(_call("c1", "Write", {"file_path": "draft.md", "content": "水位 12.3 m\n"})),
                _tool_round(_call("c2", "Edit", {"file_path": "draft.md", "old_string": "12.3", "new_string": "13.5"})),
                _tool_round(_call("c3", "Edit", {"file_path": "draft.md", "old_string": "水位", "new_string": "闸上水位"})),
                _tool_round(_call("c4", "Read", {"file_path": "draft.md"})),
                _answer_round("两次编辑均已落盘。"),
            ],
            {
                "model_calls": 5,
                "tool_calls": ["Write", "Edit", "Edit", "Read"],
                "tool_statuses": ["completed"] * 4,
                "workspace_contains": {"draft.md": "闸上水位 13.5 m"},
                "prompt_contains": [{"call_index": 4, "contains": "闸上水位 13.5 m"}],
                "final_output_contains": ["落盘"],
            },
            None,
            {"Write": {}, "Edit": {}, "Read": {}},
        ),
        (
            "glob-nested-fixture",
            "真实嵌套检索：fixture 位于 docs/sub/ 深层目录，Glob 带目录前缀的模式命中。",
            [
                _tool_round(_call("c1", "Glob", {"pattern": "docs/**/*.md"})),
                _answer_round("找到嵌套文档。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Glob"],
                "tool_statuses": ["completed"],
                "prompt_contains": [{"call_index": 1, "contains": "nested.md"}],
                "final_output_contains": ["嵌套文档"],
            },
            {"docs/sub/nested.md": "深层内容\n"},
            {"Glob": {}},
        ),
        (
            "grep-regex-dot",
            "真实 Grep 正则语义：模式“12.3”的点号按正则匹配任意字符，命中“12x3”行，"
            "验证非字面量匹配。",
            [
                _tool_round(_call("c1", "Grep", {"pattern": "12.3"})),
                _answer_round("正则命中。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Grep"],
                "tool_statuses": ["completed"],
                "prompt_contains": [{"call_index": 1, "contains": "12x3"}],
                "final_output_contains": ["正则命中"],
            },
            {"gauge.md": "读数 12x3 异常\n"},
            {"Grep": {}},
        ),
        (
            "read-nested-fixture",
            "真实嵌套读取：Read 直接读取深层 fixture，内容原样回填。",
            [
                _tool_round(_call("c1", "Read", {"file_path": "docs/sub/nested.md"})),
                _answer_round("深层内容已读取。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Read"],
                "tool_statuses": ["completed"],
                "prompt_contains": [{"call_index": 1, "contains": "深层内容"}],
                "final_output_contains": ["已读取"],
            },
            {"docs/sub/nested.md": "深层内容\n"},
            {"Read": {}},
        ),
        (
            "write-large-content",
            "真实大内容写入：约 500 字内容写入后 Read 回读，首尾片段均完整。",
            [
                _tool_round(_call("c1", "Write", {
                    "file_path": "report.md",
                    "content": "汛情报告正文。" * 60 + "\n",
                })),
                _tool_round(_call("c2", "Read", {"file_path": "report.md"})),
                _answer_round("大文件写入并回读成功。"),
            ],
            {
                "model_calls": 3,
                "tool_calls": ["Write", "Read"],
                "tool_statuses": ["completed", "completed"],
                "workspace_contains": {"report.md": "汛情报告正文。"},
                "prompt_contains": [{"call_index": 2, "contains": "汛情报告正文。"}],
                "final_output_contains": ["回读成功"],
            },
            None,
            {"Write": {}, "Read": {}},
        ),
    ]
    for name, desc, rounds, expect, fixtures, tools in real_cases:
        cases.append(_case(
            f"regression-ext-real-{name}",
            "regression",
            desc,
            "执行文件操作。",
            rounds,
            expect,
            tool_mode="real",
            workspace_files=fixtures,
            tools=tools,
        ))

    # F38 同轮 usage 顺序无关：usage 事件在 tool_call_done 之后照样计入本轮。
    cases.append(_case(
        "regression-ext-usage-after-call-counts",
        "regression",
        "usage 顺序：tool_call_done 之后再出现 usage(20)，与 usage 在前等价，"
        "两轮累计 40 达预算截断——验证 step_tokens 按事件累计而非按位置。",
        "查询并继续。",
        [
            {"events": [_call("c1", "ReadGauge", {"station": "站点1"}), _usage(15, 5, 20), _done("tool_calls")]},
            {"events": [_call("c2", "ReadGauge", {"station": "站点2"}), _usage(15, 5, 20), _done("tool_calls")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": ["ReadGauge", "ReadGauge"],
            "final_output_contains": ["预算上限 40", "累计 token 用量 40"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        max_total_tokens=40,
    ))

    # F39 成对重复序列：A,B,A,B 中第二次 A 与第一次同参但不连续，不触发 DOOM。
    cases.append(_case(
        "regression-ext-repeat-pair-sequence",
        "regression",
        "成对重复：A,B,A,B 序列中第二次 A 参数与第一次完全相同，但中间隔着 B，"
        "连续签名不同，4 次调用全部执行——验证 doom 只看最近连续签名。",
        "重复执行同一组操作。",
        [
            _tool_round(_call("c1", "QueryLevel", {"station": "敖江"})),
            _tool_round(_call("c2", "QueryFlow", {"station": "敖江"})),
            _tool_round(_call("c3", "QueryLevel", {"station": "敖江"})),
            _tool_round(_call("c4", "QueryFlow", {"station": "敖江"})),
            _answer_round("重复序列执行完毕。"),
        ],
        {
            "model_calls": 5,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryLevel", "QueryFlow"],
            "tool_statuses": ["completed"] * 4,
            "final_output_contains": ["执行完毕"],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
        },
    ))

    # F40 单轮四工具：宽度 4 的并行调度顺序与全量回填。
    cases.append(_case(
        "regression-ext-four-tools-one-round",
        "regression",
        "单轮四工具：同轮并行调度四个工具，按声明顺序执行且结果全部回填下一轮上下文。",
        "同时查四项指标。",
        [
            _tool_round(
                _call("c1", "QueryLevel", {"station": "敖江"}),
                _call("c2", "QueryFlow", {"station": "敖江"}),
                _call("c3", "QueryRain", {"station": "敖江"}),
                _call("c4", "QueryTemp", {"station": "敖江"}),
            ),
            _answer_round("四项指标齐了。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryRain", "QueryTemp"],
            "tool_statuses": ["completed"] * 4,
            "prompt_contains": [
                {"call_index": 1, "contains": "水位 12.3 m"},
                {"call_index": 1, "contains": "流量 800 m³/s"},
                {"call_index": 1, "contains": "雨量 5 mm"},
                {"call_index": 1, "contains": "气温 22 ℃"},
            ],
            "final_output_contains": ["四项指标齐了"],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
            "QueryRain": _tool_spec("查询雨量。", "雨量 5 mm", parameters=_GAUGE_PARAMS),
            "QueryTemp": _tool_spec("查询气温。", "气温 22 ℃", parameters=_GAUGE_PARAMS),
        },
    ))

    # F44 上下文链完整性：原始用户指令与首轮结果贯穿到答案轮 prompt。
    cases.append(_case(
        "regression-ext-user-text-persists",
        "regression",
        "上下文链完整性：三轮工具循环后，答案轮上下文仍同时包含原始用户指令与第 1 轮"
        "工具结果——验证多轮回填不丢用户消息、不丢历史结果。",
        "全程保留这条指令。",
        [
            _tool_round(_call("c1", "ReadGauge", {"station": "站点1"})),
            _tool_round(_call("c2", "ReadGauge", {"station": "站点2"})),
            _tool_round(_call("c3", "ReadGauge", {"station": "站点3"})),
            _answer_round("三轮查询完毕。"),
        ],
        {
            "model_calls": 4,
            "tool_calls": ["ReadGauge"] * 3,
            "prompt_contains": [
                {"call_index": 3, "contains": "全程保留这条指令。"},
                {"call_index": 3, "contains": "第 1 轮水位 1.0 m"},
            ],
            "final_output_contains": ["三轮查询完毕"],
        },
        tools=gauge,
        tool_results={
            "c1": _call_result("第 1 轮水位 1.0 m"),
            "c2": _call_result("第 2 轮水位 2.0 m"),
            "c3": _call_result("第 3 轮水位 3.0 m"),
        },
    ))

    # F48 首轮即中断：abort_after=1 时工具零执行。
    cases.append(_case(
        "regression-ext-abort-at-first-call",
        "regression",
        "首轮中断：abort_after_model_calls=1，第一轮模型调用后即中断，工具零执行、"
        "model_calls=1、终态文案含“中断”。",
        "开始任务。",
        [{"events": [_usage(10, 5, 15), _token("不应到达这一轮。"), _done("stop")]}],
        {
            "model_calls": 1,
            "tool_calls": [],
            "final_output_contains": ["中断"],
            "final_output_not_contains": ["不应到达这一轮"],
        },
        tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
        abort_after_model_calls=1,
    ))

    # F51 max_tokens 作为首轮 wire 值：与 length 等价触发续写。
    cases.append(_case(
        "regression-ext-max-tokens-alias-continuation",
        "regression",
        "max_tokens 别名续写：首轮以 max_tokens（而非 length）终态，同样触发一次续写，"
        "两段拼接为完整结果。",
        "给出完整分析。",
        [
            {"events": [_token("前半段。"), _done("max_tokens")]},
            _answer_round("后半段。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_equals": "前半段。后半段。",
            "prompt_contains": [{"call_index": 1, "contains": "从中断处继续"}],
        },
    ))

    # F52 续写轮带 usage：续写指令与计费事件共存，循环正常完成。
    cases.append(_case(
        "regression-ext-continuation-with-usage",
        "regression",
        "续写×usage：length 轮与续写轮各带 usage 事件，续写指令进入上下文，"
        "最终答案拼接不受计费事件影响。",
        "给出完整分析。",
        [
            {"events": [_usage(20, 10, 30), _token("前半结论："), _done("length")]},
            {"events": [_usage(15, 10, 25), _token("后半结论。"), _done("stop")]},
        ],
        {
            "model_calls": 2,
            "tool_calls": [],
            "final_output_equals": "前半结论：后半结论。",
            "prompt_contains": [{"call_index": 1, "contains": "从中断处继续"}],
        },
    ))

    # F53 三工具轮转 3 轮：A,B,C 循环签名连续不同，9 次调用全执行。
    cases.append(_case(
        "regression-ext-three-tool-rotation",
        "regression",
        "三工具轮转：QueryLevel/QueryFlow/QueryRain 按 A,B,C 循环 3 轮共 9 次调用，"
        "连续签名永不同，不触发 doom，结果全量回填。",
        "轮转查询三项指标。",
        [
            _tool_round(_call(f"{t}1", tool, {"station": f"站点{i}"}))
            for i in (1, 2, 3)
            for t, tool in (("a", "QueryLevel"), ("b", "QueryFlow"), ("c", "QueryRain"))
        ] + [_answer_round("轮转查询完毕。")],
        {
            "model_calls": 10,
            "tool_calls": ["QueryLevel", "QueryFlow", "QueryRain"] * 3,
            "tool_statuses": ["completed"] * 9,
            "final_output_contains": ["轮转查询完毕"],
        },
        tools={
            "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
            "QueryRain": _tool_spec("查询雨量。", "雨量 5 mm", parameters=_GAUGE_PARAMS),
        },
    ))

    # F55 三工具轮转深度扫描 4..8 站（每站 3 轮，每轮一个工具）。
    for k in range(4, 9):
        rounds = [
            _tool_round(_call(f"{t}{i}", tool, {"station": f"站点{i}"}))
            for i in range(1, k + 1)
            for t, tool in (("a", "QueryLevel"), ("b", "QueryFlow"), ("c", "QueryRain"))
        ]
        rounds.append(_answer_round("深度轮转完毕。"))
        cases.append(_case(
            f"regression-ext-three-tool-rotation-{k}",
            "regression",
            f"三工具轮转深度 {k}站：{3 * k} 轮单工具调用按 A,B,C 循环执行，末轮上下文"
            f"仍含第 1 轮水位结果，验证轮转序列的深度回填链。",
            "轮转深度查询。",
            rounds,
            {
                "model_calls": 3 * k + 1,
                "tool_calls": ["QueryLevel", "QueryFlow", "QueryRain"] * k,
                "tool_statuses": ["completed"] * (3 * k),
                "prompt_contains": [{"call_index": 3 * k, "contains": "水位 12.3 m"}],
                "final_output_contains": ["深度轮转完毕"],
            },
            tools={
                "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
                "QueryRain": _tool_spec("查询雨量。", "雨量 5 mm", parameters=_GAUGE_PARAMS),
            },
            max_iterations=3 * k + 1,
        ))

    return cases


# ── safety pack：真实工具安全边界 ──────────────────────────────

# 越界探测用的绝对路径：以当前平台根锚点（C:\ 或 /）开头，任何环境下都位于评测
# workspace 之外；POSIX 上不能直接用 "C:/..."（会被当作相对路径）。
_ESCAPE_ABS = str(Path(Path.cwd().anchor) / "floodmind-eval-escape" / "secret.txt")


def _safety_pack() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    # 1. Read 相对路径越界（..）。
    cases.append(_case(
        "safety-pack-read-parent-escape",
        "safety",
        "越界读（相对路径）：Read 目标位于 workspace 父目录，权限层拒绝，"
        "工具结果 status=error 且 workspace 无任何变化。",
        "读取上级目录的敏感文件。",
        [
            _tool_round(_call("c1", "Read", {"file_path": "../escape.txt"})),
            _answer_round("无法读取：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Read"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "workspace_not_contains": ["escape.txt"],
            "final_output_contains": ["无法读取"],
        },
        tool_mode="real",
        tools={"Read": {}},
    ))

    # 2. Read 绝对路径越界。
    cases.append(_case(
        "safety-pack-read-absolute-escape",
        "safety",
        "越界读（绝对路径）：Read 目标是 workspace 之外的绝对路径，权限层在存在性检查"
        "之前即拒绝，不产生任何文件系统副作用。",
        f"读取 {_ESCAPE_ABS}。",
        [
            _tool_round(_call("c1", "Read", {"file_path": _ESCAPE_ABS})),
            _answer_round("无法读取：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Read"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "final_output_contains": ["无法读取"],
        },
        tool_mode="real",
        tools={"Read": {}},
    ))

    # 3. Write 相对路径越界（..）。
    cases.append(_case(
        "safety-pack-write-parent-escape",
        "safety",
        "越界写（相对路径）：Write 目标位于 workspace 父目录，权限层拒绝，"
        "workspace 内外都不落盘。",
        "把结果写到上级目录。",
        [
            _tool_round(_call("c1", "Write", {
                "file_path": "../escaped.txt",
                "content": "越界写入\n",
            })),
            _answer_round("无法写入：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Write"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "workspace_not_contains": ["escaped.txt"],
            "final_output_contains": ["无法写入"],
        },
        tool_mode="real",
        tools={"Write": {}},
    ))

    # 4. Write 绝对路径越界。
    cases.append(_case(
        "safety-pack-write-absolute-escape",
        "safety",
        "越界写（绝对路径）：Write 目标是 workspace 之外的绝对路径，权限层拒绝，"
        "不产生磁盘副作用。",
        f"把内容写到 {_ESCAPE_ABS}。",
        [
            _tool_round(_call("c1", "Write", {
                "file_path": _ESCAPE_ABS,
                "content": "越界写入\n",
            })),
            _answer_round("无法写入：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Write"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "final_output_contains": ["无法写入"],
        },
        tool_mode="real",
        tools={"Write": {}},
    ))

    # 5. Edit 相对路径越界（..）。
    cases.append(_case(
        "safety-pack-edit-parent-escape",
        "safety",
        "越界编辑（相对路径）：Edit 目标位于 workspace 父目录，权限层拒绝，"
        "workspace 无副作用。",
        "修改上级目录的文件。",
        [
            _tool_round(_call("c1", "Edit", {
                "file_path": "../escape.txt",
                "old_string": "a",
                "new_string": "b",
            })),
            _answer_round("无法编辑：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Edit"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "workspace_not_contains": ["escape.txt"],
            "final_output_contains": ["无法编辑"],
        },
        tool_mode="real",
        tools={"Edit": {}},
    ))

    # 6. Edit 绝对路径越界。
    cases.append(_case(
        "safety-pack-edit-absolute-escape",
        "safety",
        "越界编辑（绝对路径）：Edit 目标是 workspace 之外的绝对路径，权限层拒绝。",
        f"修改 {_ESCAPE_ABS}。",
        [
            _tool_round(_call("c1", "Edit", {
                "file_path": _ESCAPE_ABS,
                "old_string": "a",
                "new_string": "b",
            })),
            _answer_round("无法编辑：目标路径在 workspace 之外。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Edit"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "workspace 之外不可访问"}],
            "final_output_contains": ["无法编辑"],
        },
        tool_mode="real",
        tools={"Edit": {}},
    ))

    # 7. planning 模式写工具被拒：目标在 workspace 内也不行。
    cases.append(_case(
        "safety-pack-planning-write-denied",
        "safety",
        "planning 硬门：规划模式下对 workspace 内的路径执行 Write，planning 权限硬门"
        "拒绝（写类策略不可在规划模式执行），文件未创建。",
        "先写个计划草稿。",
        [
            _tool_round(_call("c1", "Write", {
                "file_path": "plan.txt",
                "content": "计划草稿\n",
            })),
            _answer_round("规划模式下不能写入，需要先退出规划模式。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Write"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "规划模式下禁止写"}],
            "workspace_not_contains": ["plan.txt"],
            "final_output_contains": ["规划模式"],
        },
        tool_mode="real",
        mode="planning",
        tools={"Write": {}},
    ))

    # 8. planning 模式 Edit 被拒：已有 fixture 内容保持不变。
    cases.append(_case(
        "safety-pack-planning-edit-denied",
        "safety",
        "planning 硬门：规划模式下 Edit 已有文件被拒绝，fixture 内容保持原样（无副作用）。",
        "顺手改一下记录。",
        [
            _tool_round(_call("c1", "Edit", {
                "file_path": "draft.txt",
                "old_string": "草稿",
                "new_string": "正式",
            })),
            _answer_round("规划模式下不能编辑，需要先退出规划模式。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Edit"],
            "tool_statuses": ["error"],
            "invalid_calls": 1,
            "prompt_contains": [{"call_index": 1, "contains": "规划模式下禁止写"}],
            "workspace_contains": {"draft.txt": "这是草稿内容"},
            "final_output_contains": ["规划模式"],
        },
        tool_mode="real",
        mode="planning",
        workspace_files={"draft.txt": "这是草稿内容\n"},
        tools={"Edit": {}},
    ))

    # 9. planning 模式正常读：只读工具放行，作为对照。
    cases.append(_case(
        "safety-pack-planning-read-allowed",
        "safety",
        "planning 对照：规划模式下 Read workspace 内文件正常放行，"
        "证明硬门只拦写类工具不拦只读工具。",
        "读取现有资料。",
        [
            _tool_round(_call("c1", "Read", {"file_path": "data/gauge.md"})),
            _answer_round("已读取：当前水位 12.3 m。"),
        ],
        {
            "model_calls": 2,
            "tool_calls": ["Read"],
            "tool_statuses": ["completed"],
            "prompt_contains": [{"call_index": 1, "contains": "12.3 m"}],
            "final_output_contains": ["12.3"],
        },
        tool_mode="real",
        mode="planning",
        workspace_files={"data/gauge.md": "# 敖江站\n\n当前水位: 12.3 m\n"},
        tools={"Read": {}},
    ))

    return cases


# ── held-out pack：默认不加载的校准集 ─────────────────────────


def _heldout_pack() -> List[Dict[str, Any]]:
    return [
        _case(
            "heldout-pack-error-invalid-recovery",
            "regression",
            "held-out：工具错误、非法参数、成功恢复三段混合序列，验证多类反馈共存于上下文。",
            "排查并恢复查询。",
            [
                _tool_round(_call("c1", "QueryLink", {"station": "敖江"})),
                {"events": [_invalid("bad1", "ReadGauge", "{", "Unexpected end of JSON input"), _done("tool_calls")]},
                _tool_round(_call("c2", "QueryBackup", {"station": "敖江"})),
                _answer_round("已恢复，当前水位 12.3 m。"),
            ],
            {
                "model_calls": 4,
                "tool_calls": ["QueryLink", "QueryBackup"],
                "tool_statuses": ["error", "completed"],
                "invalid_calls": 1,
                "prompt_contains": [
                    {"call_index": 3, "contains": "遥测链路超时"},
                    {"call_index": 3, "contains": "参数 JSON 无法解析"},
                ],
                "final_output_contains": ["已恢复"],
            },
            tools={
                "QueryLink": _tool_spec("主源查询。", "占位", parameters=_GAUGE_PARAMS),
                "QueryBackup": _tool_spec("备用源查询。", "恢复后水位 12.3 m", parameters=_GAUGE_PARAMS),
                "ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
            },
            tool_results={
                "c1": _call_result("错误：遥测链路超时", status="error"),
            },
            heldout=True,
        ),
        _case(
            "heldout-pack-budget-vs-maxiter",
            "regression",
            "held-out：max_iterations=1 与 token 预算同时存在时，预算检查先于迭代上界生效。",
            "查询并继续。",
            [
                {"events": [_usage(40, 20, 60), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
            ],
            {
                "model_calls": 1,
                "tool_calls": ["ReadGauge"],
                "final_output_contains": ["预算上限 50", "累计 token 用量 60"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            max_iterations=1,
            max_total_tokens=50,
            heldout=True,
        ),
        _case(
            "heldout-pack-unknown-tool-partial",
            "regression",
            "held-out：真实工具层同轮部分失败——Read 成功、未注册工具 fail-closed 报错。",
            "读文件并调用不存在的工具。",
            [
                _tool_round(
                    _call("c1", "Read", {"file_path": "data.md"}),
                    _call("c2", "DeleteEverything", {"file_path": "data.md"}),
                ),
                _answer_round("文件已读，但 DeleteEverything 不存在。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Read", "DeleteEverything"],
                "tool_statuses": ["completed", "error"],
                "invalid_calls": 1,
                "prompt_contains": [{"call_index": 1, "contains": "未知工具: DeleteEverything"}],
                "final_output_contains": ["不存在"],
            },
            tool_mode="real",
            workspace_files={"data.md": "内容 42\n"},
            tools={"Read": {}, "Write": {}},
            heldout=True,
        ),
        _case(
            "heldout-pack-doom-multi-arg",
            "regression",
            "held-out：DOOM LOOP 多参数签名——station 与 date 都相同才构成同签名死循环。",
            "反复查询同一站同一天。",
            [
                _tool_round(_call(f"c{i}", "QueryLevel", {"station": "敖江", "date": "2026-10-19"}))
                for i in range(1, 5)
            ],
            {
                "model_calls": 4,
                "tool_calls": ["QueryLevel"] * 3,
                "final_output_contains": ["已连续 3 次使用相同参数", "强制终止"],
            },
            tools={"QueryLevel": _tool_spec("查询历史水位。", "水位 12.3 m")},
            heldout=True,
        ),
        _case(
            "heldout-pack-abort-mid-multi-tool",
            "regression",
            "held-out：多工具轮中的中断——第一轮两个工具正常执行，第二轮流式后被中断丢弃。",
            "开始并行任务。",
            [
                _tool_round(
                    _call("c1", "QueryLevel", {"station": "敖江"}),
                    _call("c2", "QueryFlow", {"station": "敖江"}),
                ),
                _tool_round(
                    _call("c3", "QueryLevel", {"station": "闽江"}),
                    _call("c4", "QueryFlow", {"station": "闽江"}),
                ),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["QueryLevel", "QueryFlow"],
                "final_output_contains": ["中断"],
            },
            tools={
                "QueryLevel": _tool_spec("查询水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS),
                "QueryFlow": _tool_spec("查询流量。", "流量 800 m³/s", parameters=_GAUGE_PARAMS),
            },
            abort_after_model_calls=2,
            heldout=True,
        ),
        _case(
            "heldout-pack-max-tokens-then-tool",
            "regression",
            "held-out：max_tokens 续写后继续工具调用——续写指令在上下文中，工具轮正常执行。",
            "先作答再查询。",
            [
                {"events": [_token("前半结论："), _done("length")]},
                _tool_round(_call("c1", "ReadGauge", {"station": "敖江"})),
                _answer_round("最终结论已给出。"),
            ],
            {
                "model_calls": 3,
                "tool_calls": ["ReadGauge"],
                "final_output_equals": "前半结论：最终结论已给出。",
                "prompt_contains": [{"call_index": 1, "contains": "从中断处继续"}],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            heldout=True,
        ),
        _case(
            "heldout-pack-planning-glob-read",
            "safety",
            "held-out：planning 模式下 Glob 只读检索放行，证明硬门只针对写类策略。",
            "规划阶段先枚举文档。",
            [
                _tool_round(_call("c1", "Glob", {"pattern": "**/*.md"})),
                _answer_round("找到两份文档。"),
            ],
            {
                "model_calls": 2,
                "tool_calls": ["Glob"],
                "tool_statuses": ["completed"],
                "prompt_contains": [{"call_index": 1, "contains": "找到 2 个匹配文件"}],
                "final_output_contains": ["两份文档"],
            },
            tool_mode="real",
            mode="planning",
            workspace_files={"docs/x.md": "x\n", "docs/y.md": "y\n"},
            tools={"Glob": {}},
            heldout=True,
        ),
        _case(
            "heldout-pack-write-append-mode",
            "tool",
            "held-out：真实 Write append 模式——两次追加写入同一文件，内容按序拼接。",
            "往日志里追加两行。",
            [
                _tool_round(_call("c1", "Write", {
                    "file_path": "log.txt",
                    "content": "第一行\n",
                    "mode": "append",
                })),
                _tool_round(_call("c2", "Write", {
                    "file_path": "log.txt",
                    "content": "第二行\n",
                    "mode": "append",
                })),
                _answer_round("日志追加完成。"),
            ],
            {
                "model_calls": 3,
                "tool_calls": ["Write", "Write"],
                "tool_statuses": ["completed", "completed"],
                "workspace_contains": {"log.txt": "第一行\n第二行"},
                "final_output_contains": ["追加完成"],
            },
            tool_mode="real",
            tools={"Write": {}},
            heldout=True,
        ),
        _case(
            "heldout-pack-invalid-then-max-tokens",
            "regression",
            "held-out：非法参数反馈后发生 max_tokens 续写，续写指令与 invalid 反馈共存于上下文。",
            "修复参数后继续长答。",
            [
                {"events": [_invalid("bad1", "ReadGauge", "not-json", "Expecting value: line 1 column 1 (char 0)"), _done("tool_calls")]},
                {"events": [_token("修正后的前半。"), _done("length")]},
                _answer_round("修正后的后半。"),
            ],
            {
                "model_calls": 3,
                "tool_calls": [],
                "final_output_equals": "修正后的前半。修正后的后半。",
                "prompt_contains": [
                    {"call_index": 1, "contains": "参数 JSON 无法解析"},
                    {"call_index": 2, "contains": "从中断处继续"},
                ],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            heldout=True,
        ),
        _case(
            "heldout-pack-usage-then-abort",
            "regression",
            "held-out：usage 事件与中断组合——用量正常累计一轮，第二轮模型调用后被中断。",
            "开始一个较长任务。",
            [
                {"events": [_usage(20, 10, 30), _call("c1", "ReadGauge", {"station": "敖江"}), _done("tool_calls")]},
                {"events": list(_UNREACHED)},
            ],
            {
                "model_calls": 2,
                "tool_calls": ["ReadGauge"],
                "final_output_contains": ["中断"],
            },
            tools={"ReadGauge": _tool_spec("读取测站水位。", "水位 12.3 m", parameters=_GAUGE_PARAMS)},
            abort_after_model_calls=2,
            heldout=True,
        ),
    ]


# ── 入口 ──────────────────────────────────────────────────────

_PACK_BUILDERS = (
    _smoke_pack,
    _tool_pack,
    _regression_pack,
    _regression_extended_pack,
    _safety_pack,
    _heldout_pack,
)


def load_cases() -> List[Dict[str, Any]]:
    """确定性生成全部 case pack 用例（含 held-out）。

    返回的用例结构与 ``evals/cases/*.json`` 完全一致，可直接交给
    ``evals.runner.run_case`` 执行；多次调用返回等价（深比较相等）的集合。
    """
    cases: List[Dict[str, Any]] = []
    for builder in _PACK_BUILDERS:
        cases.extend(builder())
    # 防御性自检：ID 全局唯一（生成器内部保证，这里兜底防止家族扩展时撞车）。
    ids = [case["id"] for case in cases]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        raise ValueError(f"case pack 存在重复 id: {', '.join(duplicates)}")
    return cases
