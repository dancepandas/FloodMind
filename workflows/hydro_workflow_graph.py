"""局部水文工作流图：获取 skill -> 分析输入 -> 构造输入 -> 执行 skill -> 整理结果 -> 校验 -> 交付。"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, TypedDict

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - optional dependency fallback
    END = "__end__"
    StateGraph = None


class HydroWorkflowState(TypedDict, total=False):
    user_request: str
    input_source_type: str
    case_name: str
    case_skill_name: str
    case_requirement_summary: str
    inspected_input_summary: str
    need_input_prep: bool
    need_model_run: bool
    need_result_organize: bool
    intermediate_result: str
    model_result: str
    organized_result: str
    validation_result: Dict[str, Any]
    workflow_summary: str
    final_answer: str
    workflow_plan: List[Dict[str, Any]]
    workflow_steps: Dict[str, Dict[str, Any]]
    notes: List[str]
    errors: List[str]


class HydroWorkflowGraph:
    """对现有 specialist/skill 做轻量 LangGraph 编排。"""

    NODE_DEFAULT_TITLES = {
        "resolve_case_context": "分析案例 skill 要求",
        "inspect_input": "分析输入数据与约束",
        "prepare_input": "构造标准输入文件",
        "run_model": "执行水文模型",
        "organize_result": "整理最终交付结果",
        "validate_result": "校验完成度",
        "summarize_workflow": "总结整个流程与结果",
    }

    def __init__(
        self,
        *,
        run_python_specialist: Callable[[str], str],
        run_excel_specialist: Callable[[str], str],
        run_validator: Callable[[str], str],
        parse_validator_output: Callable[[str], Dict[str, Any]],
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._run_python_specialist = run_python_specialist
        self._run_excel_specialist = run_excel_specialist
        self._run_validator = run_validator
        self._parse_validator_output = parse_validator_output
        self._event_sink = event_sink
        self._compiled_graph = self._build_graph()

    @staticmethod
    def should_handle(user_request: str) -> bool:
        text = (user_request or "").lower()
        forecast_markers = (
            "预报", "预测", "forecast", "future flow", "未来流量", "入库流量", "出口流量",
        )
        if not any(marker in text for marker in forecast_markers):
            return False
        plotting_only_markers = ("过程线图", "绘图", "plot", "png", "图片")
        return not (any(marker in text for marker in plotting_only_markers) and "input.json" not in text and "预报" not in text)

    @staticmethod
    def _choose_result_specialist(user_request: str) -> str:
        text = (user_request or "").lower()
        excel_markers = ("excel", ".xlsx", ".xls", "工作表", "sheet", "表格", "导出表")
        if any(marker in text for marker in excel_markers):
            return "excel"
        return "python"

    def run(self, user_request: str) -> str:
        initial_state: HydroWorkflowState = {
            "user_request": user_request,
            "notes": [],
            "errors": [],
            "workflow_plan": [],
            "workflow_steps": {},
        }
        if self._compiled_graph is not None:
            result = self._compiled_graph.invoke(initial_state)
        else:
            result = self._run_sequential(initial_state)
        return result.get("final_answer") or result.get("organized_result") or result.get("model_result") or result.get("intermediate_result") or "未生成工作流结果。"

    def stream(self, user_request: str) -> Iterator[Dict[str, Any]]:
        event_queue: queue.Queue = queue.Queue()

        def sink(event: Dict[str, Any]) -> None:
            event_queue.put(("event", event))

        original_sink = self._event_sink
        self._event_sink = sink

        def runner() -> None:
            try:
                final_answer = self.run(user_request)
                event_queue.put(("final", final_answer))
            except Exception as exc:
                logger.error("HydroWorkflowGraph 流式执行失败: %s", exc, exc_info=True)
                event_queue.put(("error", str(exc)))
            finally:
                event_queue.put(("done", None))

        worker = threading.Thread(target=runner, daemon=True)
        worker.start()

        try:
            while True:
                kind, payload = event_queue.get()
                if kind == "event":
                    yield payload
                elif kind == "final":
                    yield {"type": "token", "content": payload}
                elif kind == "error":
                    yield {"type": "reasoning", "content": f"抱歉，工作流执行失败：{payload}"}
                elif kind == "done":
                    break
        finally:
            self._event_sink = original_sink

    def _emit(self, event: Dict[str, Any]) -> None:
        if self._event_sink:
            self._event_sink(event)

    def _emit_reasoning(self, content: str) -> None:
        text = (content or "").strip()
        if text:
            self._emit({"type": "reasoning", "content": text + "\n"})

    def _emit_workflow_plan(self, state: HydroWorkflowState) -> None:
        steps = state.get("workflow_plan") or []
        self._emit(
            {
                "type": "workflow_plan",
                "title": "Hydro Workflow",
                "steps": steps,
            }
        )

    @classmethod
    def _ordered_workflow_nodes(cls, state: HydroWorkflowState) -> List[str]:
        nodes = ["resolve_case_context", "inspect_input"]
        if state.get("need_input_prep"):
            nodes.append("prepare_input")
        if state.get("need_model_run"):
            nodes.append("run_model")
        if state.get("need_result_organize"):
            nodes.append("organize_result")
        nodes.extend(["validate_result", "summarize_workflow"])
        return nodes

    @classmethod
    def _workflow_label_map(cls, state: HydroWorkflowState) -> Dict[str, str]:
        return {node: f"{index + 1}." for index, node in enumerate(cls._ordered_workflow_nodes(state))}

    @classmethod
    def _workflow_prefix(cls, state: HydroWorkflowState, node_key: str) -> str:
        labels = cls._workflow_label_map(state)
        ordered = cls._ordered_workflow_nodes(state)
        try:
            index = ordered.index(node_key) + 1
        except ValueError:
            index = 0
        return f"第 {index} 步" if index else node_key

    @classmethod
    def _default_plan_steps(cls, state: HydroWorkflowState) -> List[Dict[str, Any]]:
        labels = cls._workflow_label_map(state)
        return [
            {
                "key": node,
                "label": labels.get(node, ""),
                "title": cls.NODE_DEFAULT_TITLES.get(node, node),
                "detail": "",
                "status": "pending",
                "outcome": "",
            }
            for node in cls._ordered_workflow_nodes(state)
        ]

    @classmethod
    def _parse_workflow_plan(cls, raw_text: str, state: HydroWorkflowState) -> List[Dict[str, Any]]:
        defaults = cls._default_plan_steps(state)
        try:
            payload = json.loads((raw_text or "").strip())
        except Exception:
            return defaults

        steps_payload = payload.get("steps") if isinstance(payload, dict) else None
        if not isinstance(steps_payload, list):
            return defaults

        allowed = cls._ordered_workflow_nodes(state)
        labels = cls._workflow_label_map(state)
        parsed_steps: List[Dict[str, Any]] = []
        for item in steps_payload:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "") or "").strip()
            if key not in allowed:
                continue
            title = str(item.get("title", "") or "").strip() or cls.NODE_DEFAULT_TITLES.get(key, key)
            detail = str(item.get("detail", "") or "").strip()
            parsed_steps.append(
                {
                    "key": key,
                    "label": labels.get(key, ""),
                    "title": title,
                    "detail": detail,
                    "status": "pending",
                    "outcome": "",
                }
            )

        if not parsed_steps:
            return defaults

        merged: List[Dict[str, Any]] = []
        parsed_by_key = {step["key"]: step for step in parsed_steps}
        for default_step in defaults:
            merged.append(parsed_by_key.get(default_step["key"], default_step))
        return merged

    def _plan_workflow(self, state: HydroWorkflowState) -> HydroWorkflowState:
        allowed_nodes = self._ordered_workflow_nodes(state)
        defaults = self._default_plan_steps(state)
        task = (
            "请根据当前用户需求和系统推断出的执行路径，规划本次 Hydro workflow 的用户可见任务步骤。"
            "要求：仅基于给定节点输出 JSON，不要添加解释；每个 title 必须是本次任务语境下的具体动作，而不是泛化标题。"
            "JSON 格式：{\"steps\":[{\"key\":\"节点名\",\"title\":\"步骤标题\",\"detail\":\"补充说明\"}]}."
            f"允许节点：{allowed_nodes}\n\n"
            f"[用户需求]\n{state.get('user_request', '')}\n\n"
            f"[输入来源]\n{state.get('input_source_type', '')}\n\n"
            f"[案例]\n{state.get('case_name', '')}\n"
        )
        try:
            raw_plan = self._run_python_specialist(task)
            state["workflow_plan"] = self._parse_workflow_plan(raw_plan, state)
        except Exception:
            state["workflow_plan"] = defaults
        return state

    def _update_step_state(
        self,
        state: HydroWorkflowState,
        step_key: str,
        *,
        status: str,
        title: Optional[str] = None,
        detail: str = "",
        outcome: str = "",
    ) -> None:
        workflow_steps = state.setdefault("workflow_steps", {})
        current = workflow_steps.get(step_key, {})
        step_label = self._workflow_label_map(state).get(step_key, step_key)

        planned_title = ""
        for step in state.get("workflow_plan") or []:
            if step.get("key") == step_key:
                planned_title = str(step.get("title", "") or "").strip()
                break

        current["status"] = status
        if not current.get("title"):
            current["title"] = planned_title or title or self.NODE_DEFAULT_TITLES.get(step_key, step_key)
        elif not planned_title and title and not str(current.get("title", "")).strip():
            current["title"] = title
        if detail:
            current["detail"] = detail
        if outcome:
            current["outcome"] = outcome
        workflow_steps[step_key] = current

        workflow_plan = state.get("workflow_plan") or []
        for step in workflow_plan:
            if step.get("key") == step_key:
                step["label"] = step_label
                step["status"] = status
                if not step.get("title"):
                    step["title"] = planned_title or title or self.NODE_DEFAULT_TITLES.get(step_key, step_key)
                if detail:
                    step["detail"] = detail
                if outcome:
                    step["outcome"] = outcome
                break

        payload = {
            "type": "workflow_step",
            "step_key": step_key,
            "label": step_label,
            "status": status,
            "title": current.get("title", "待分析"),
            "detail": current.get("detail", ""),
            "outcome": current.get("outcome", ""),
        }
        self._emit(payload)

    @staticmethod
    def _derive_step_title(output: str, fallback: str = "待分析") -> str:
        text = HydroWorkflowGraph._extract_stage_summary(output)
        if not text:
            return fallback

        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[\-\*\d\.、\s]+", "", line)
            line = re.sub(r"^(结果摘要|数据预览摘要|需求梳理摘要|校验状态|总结|summary)[:：]\s*", "", line, flags=re.IGNORECASE)
            if line:
                lines.append(line)
        if not lines:
            return fallback

        title = lines[0]
        if len(title) > 36:
            title = title[:36].rstrip() + "..."
        return title

    @staticmethod
    def _derive_validation_title(validation: Dict[str, Any], output: str) -> str:
        return HydroWorkflowGraph._derive_step_title(output, "已完成结果校验")

    @staticmethod
    def _derive_validation_outcome(validation: Dict[str, Any]) -> str:
        status = str(validation.get("overall_status", "") or "").strip().lower()
        goal_met = validation.get("is_final_goal_met")
        final_goal = str(validation.get("final_goal", "") or "").strip()
        if status == "pass" and goal_met is True:
            return "已满足最终目标"
        if final_goal:
            return f"未满足最终目标：{final_goal}"
        if status:
            return f"校验结果：{status}"
        return ""

    @staticmethod
    def _is_validation_only_text(text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return True
        markers = ("校验状态：", "是否完成最终目标：", "未完成目标：")
        return all(any(line.startswith(marker) for marker in markers) for line in normalized.splitlines() if line.strip())

    @staticmethod
    def _is_low_quality_summary(text: str) -> bool:
        normalized = (text or "").strip()
        if HydroWorkflowGraph._is_validation_only_text(normalized):
            return True
        if not normalized:
            return True
        meaningful_lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not meaningful_lines:
            return True
        bad_tokens = {"fail", "pass", "true", "false", "none", "null"}
        if all(line.lower() in bad_tokens for line in meaningful_lines):
            return True
        if sum(1 for line in meaningful_lines if line.lower() in bad_tokens) >= max(3, len(meaningful_lines) - 1):
            return True
        return False

    @staticmethod
    def _extract_artifact_names(*texts: str) -> List[str]:
        filenames: List[str] = []
        seen: set[str] = set()
        pattern = re.compile(r"([\w\-./\\]+\.(?:xlsx|xls|csv|json|png|jpg|jpeg))", re.IGNORECASE)
        for text in texts:
            for match in pattern.findall(text or ""):
                filename = match.replace('\\', '/').split('/')[-1]
                if filename and filename not in seen:
                    seen.add(filename)
                    filenames.append(filename)
        return filenames

    def _build_fallback_workflow_summary(self, state: HydroWorkflowState) -> str:
        validation = state.get("validation_result") or {}
        workflow_steps = state.get("workflow_steps") or {}
        completed_lines: List[str] = []
        for step_key in self._ordered_workflow_nodes(state):
            if step_key == "summarize_workflow":
                continue
            step_state = workflow_steps.get(step_key, {})
            title = str(step_state.get("title", "") or "").strip()
            if title:
                completed_lines.append(f"- {title}")

        artifact_names = self._extract_artifact_names(
            state.get("organized_result", ""),
            state.get("model_result", ""),
            state.get("intermediate_result", ""),
        )

        lines = ["本次工作流已完成以下处理："]
        if completed_lines:
            lines.extend(completed_lines[:6])
        else:
            lines.append("- 已完成水文工作流主要步骤执行。")

        if artifact_names:
            lines.append("")
            lines.append("最终结果：")
            for name in artifact_names[:6]:
                lines.append(f"- 已生成 `{name}`")

        lines.append("")
        if str(validation.get("overall_status", "") or "").strip().lower() == "fail":
            final_goal = str(validation.get("final_goal", "") or "").strip()
            lines.append("当前结论：尚未完全满足用户最终目标。")
            if final_goal:
                lines.append(f"仍需补充：{final_goal}")
        else:
            lines.append("当前结论：结果已生成，且已完成最终目标。")
        return "\n".join(lines).strip()

    def _build_summary_context(self, state: HydroWorkflowState) -> str:
        validation = state.get("validation_result") or {}
        workflow_steps = state.get("workflow_steps") or {}
        workflow_plan = {step.get("key"): step for step in state.get("workflow_plan") or []}
        step_lines = []
        for step_key in self._ordered_workflow_nodes(state):
            if step_key == "summarize_workflow":
                continue
            step_label = self._workflow_prefix(state, step_key)
            planned = workflow_plan.get(step_key, {})
            step_state = workflow_steps.get(step_key, {})
            title = str(planned.get("title", "") or step_state.get("title", "") or self.NODE_DEFAULT_TITLES.get(step_key, step_key)).strip()
            detail = str(step_state.get("detail", "") or "").strip()
            outcome = str(step_state.get("outcome", "") or "").strip()
            parts = [part for part in (detail, outcome) if part]
            suffix = f" ({' | '.join(parts)})" if parts else ""
            step_lines.append(f"- {step_label} {title}{suffix}")

        organized = state.get("organized_result", "")
        model_result = state.get("model_result", "")
        intermediate = state.get("intermediate_result", "")
        return (
            f"[原始用户需求]\n{state.get('user_request', '')}\n\n"
            f"[步骤完成情况]\n{'\n'.join(step_lines)}\n\n"
            f"[输入准备结果]\n{intermediate}\n\n"
            f"[模型执行结果]\n{model_result}\n\n"
            f"[结果整理结果]\n{organized}\n\n"
            f"[校验结果]\n{json.dumps(validation, ensure_ascii=False, indent=2)}"
        )

    def _generate_workflow_summary(self, state: HydroWorkflowState) -> str:
        context = self._build_summary_context(state)
        primary_prompt = (
            "请基于以下完整工作流上下文，输出面向用户的最终总结。"
            "必须说明：整个流程做了哪些工作、最终生成了什么结果、是否完成用户目标；"
            "如果未完成，要明确缺口。不要输出 JSON，不要只写 pass/fail，不要只重复校验字段。"
            "正文尽量 4-8 行，语言自然清晰。\n\n"
            f"{context}"
        )
        first_pass = self._run_python_specialist(primary_prompt)
        first_text = self._extract_stage_summary(first_pass)
        if not self._is_low_quality_summary(first_text):
            return first_text.strip()

        retry_prompt = (
            "你上一次输出的总结质量不合格。请重新总结整个 workflow，严格遵守："
            "1. 不要输出 pass/fail/true/false 之类孤立词；"
            "2. 不要只列校验状态；"
            "3. 必须写出已经完成的工作、最终产物、以及是否完成用户目标；"
            "4. 如果未完成，明确说明还差什么。"
            "直接输出给用户看的最终总结正文。\n\n"
            f"{context}"
        )
        second_pass = self._run_python_specialist(retry_prompt)
        second_text = self._extract_stage_summary(second_pass)
        if not self._is_low_quality_summary(second_text):
            return second_text.strip()
        return self._build_fallback_workflow_summary(state)

    @staticmethod
    def _extract_stage_summary(output: str) -> str:
        text = (output or "").strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                validation = payload.get("validation")
                if isinstance(validation, dict):
                    overall_status = str(validation.get("overall_status", "unknown") or "unknown")
                    is_final_goal_met = validation.get("is_final_goal_met")
                    final_goal = str(validation.get("final_goal", "") or "").strip()
                    lines = [
                        f"校验状态：{overall_status}",
                        f"是否完成最终目标：{is_final_goal_met}",
                    ]
                    if final_goal:
                        lines.append(f"未完成目标：{final_goal}")
                    return "\n".join(lines)
                summary = str(payload.get("summary", "") or "").strip()
                if summary:
                    return summary
        except Exception:
            pass
        return text

    def _emit_stage_result(self, step_label: str, output: str) -> None:
        summary = self._extract_stage_summary(output)
        if not summary:
            return
        condensed = summary.strip()
        if len(condensed) > 1200:
            condensed = condensed[:1200].rstrip() + "\n\n[本步结果较长，已截断显示]"
        self._emit_reasoning(f"{step_label}结果摘要：\n{condensed}")

    def _build_graph(self):
        if StateGraph is None:
            return None

        graph = StateGraph(HydroWorkflowState)
        graph.add_node("classify_request", self._node_classify_request)
        graph.add_node("resolve_case_context", self._node_resolve_case_context)
        graph.add_node("inspect_input", self._node_inspect_input)
        graph.add_node("prepare_input", self._node_prepare_input)
        graph.add_node("run_model", self._node_run_model)
        graph.add_node("organize_result", self._node_organize_result)
        graph.add_node("validate_result", self._node_validate_result)
        graph.add_node("summarize_workflow", self._node_summarize_workflow)
        graph.add_node("finalize_response", self._node_finalize_response)

        graph.set_entry_point("classify_request")
        graph.add_conditional_edges(
            "classify_request",
            self._route_after_classify,
            {
                "resolve_case_context": "resolve_case_context",
                "finalize_response": "finalize_response",
            },
        )
        graph.add_edge("resolve_case_context", "inspect_input")
        graph.add_conditional_edges(
            "inspect_input",
            self._route_after_inspect,
            {
                "prepare_input": "prepare_input",
                "run_model": "run_model",
                "finalize_response": "finalize_response",
            },
        )
        graph.add_conditional_edges(
            "prepare_input",
            self._route_after_prepare,
            {
                "run_model": "run_model",
                "finalize_response": "finalize_response",
            },
        )
        graph.add_conditional_edges(
            "run_model",
            self._route_after_run_model,
            {
                "organize_result": "organize_result",
                "validate_result": "validate_result",
            },
        )
        graph.add_edge("organize_result", "validate_result")
        graph.add_edge("validate_result", "summarize_workflow")
        graph.add_edge("summarize_workflow", "finalize_response")
        graph.add_edge("finalize_response", END)
        return graph.compile()

    def _run_sequential(self, state: HydroWorkflowState) -> HydroWorkflowState:
        state = self._node_classify_request(state)
        next_step = self._route_after_classify(state)
        if next_step == "resolve_case_context":
            state = self._node_resolve_case_context(state)
            state = self._node_inspect_input(state)
            next_step = self._route_after_inspect(state)
        if next_step == "prepare_input":
            state = self._node_prepare_input(state)
            next_step = self._route_after_prepare(state)
        if next_step == "run_model":
            state = self._node_run_model(state)
            next_step = self._route_after_run_model(state)
        if next_step == "organize_result":
            state = self._node_organize_result(state)
        if next_step in {"validate_result", "organize_result"}:
            state = self._node_validate_result(state)
            state = self._node_summarize_workflow(state)
        state = self._node_finalize_response(state)
        return state

    def _node_classify_request(self, state: HydroWorkflowState) -> HydroWorkflowState:
        text = state.get("user_request", "")
        lowered = text.lower()
        input_source_type = "uploaded_file" if "[已上传的文件]" in text else "text"
        case_name = "aojiang" if "敖江" in text else "jingzhou" if "靖州" in text else "aojiang"
        need_result_organize = True
        need_input_prep = any(marker in lowered for marker in ("input.json", "请求体", "中间 excel", "降雨", "流量", "预报"))
        case_skill_name = f"{case_name}-hydro-intake"
        state.update(
            {
                "input_source_type": input_source_type,
                "case_name": case_name,
                "case_skill_name": case_skill_name,
                "need_input_prep": need_input_prep,
                "need_model_run": True,
                "need_result_organize": need_result_organize,
            }
        )
        state = self._plan_workflow(state)
        self._emit_workflow_plan(state)
        self._emit_reasoning(f"[HydroWorkflow] 已识别为 {case_name} 工作流，输入来源={input_source_type}，case skill={case_skill_name}。")
        return state

    def _node_resolve_case_context(self, state: HydroWorkflowState) -> HydroWorkflowState:
        case_name = state.get("case_name", "aojiang")
        case_skill_name = state.get("case_skill_name", f"{case_name}-hydro-intake")
        request = state.get("user_request", "")
        task = (
            f"请先获取 {case_skill_name} 对应的 skill 说明，并只围绕用户最终目标提炼关键要求。"
            "重点明确：输入文件格式、必填字段、stationCode/站点规则、脚本入口、输出形式、限制条件、是否必须先生成标准输入文件。"
            f"请返回一个简洁但可执行的 requirement summary，供后续节点复用。\n\n[原始用户需求]\n{request}"
        )
        self._update_step_state(
            state,
            "resolve_case_context",
            status="running",
            detail="正在获取案例 skill，并提炼输入约束与交付要求。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'resolve_case_context')}：先获取与用户最终目标相关的 skill，并提炼输入格式、关键字段、站点规则、脚本入口和输出要求。")
        self._emit({"type": "tool_status", "tool_name": "delegate_python_specialist", "status": "running"})
        output = self._run_python_specialist(task)
        self._emit({"type": "tool_result", "tool_name": "delegate_python_specialist", "content": output})
        state["case_requirement_summary"] = output
        self._update_step_state(
            state,
            "resolve_case_context",
            status="completed",
            title=self._derive_step_title(output, "已完成 skill 要求分析"),
            detail="已提炼案例 skill 的关键要求。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'resolve_case_context'), output)
        self._emit_reasoning("已完成 skill 关键信息提炼，接下来分析用户提供的数据或原始需求。")
        return state

    def _node_inspect_input(self, state: HydroWorkflowState) -> HydroWorkflowState:
        request = state.get("user_request", "")
        input_source_type = state.get("input_source_type", "text")
        requirement_summary = state.get("case_requirement_summary", "")
        if input_source_type == "uploaded_file":
            task = (
                "请先分析用户上传的数据文件，再决定下一步。"
                "必须先确认：列名、行数、时间跨度、单位、缺失值、时间分界、是否已有阶段列、是否已有 stationCode/站点名称、是否需要补 stationCode。"
                "结合 case skill 约束，输出 inspection summary，并说明后续应如何构造该 skill 要求的标准输入文件。\n\n"
                f"[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}"
            )
        else:
            task = (
                "请根据自然语言需求先梳理结构化输入准备所需的参数。"
                "你需要先明确 forecastTime、historyDuration、futureDuration、stationCode/站点范围、模型与预期产物，"
                "并输出 inspection summary，说明后续如何构造该 skill 要求的标准输入文件。\n\n"
                f"[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}"
            )
        self._update_step_state(
            state,
            "inspect_input",
            status="running",
            detail="正在分析输入数据结构、时间范围和后续输入构建方式。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'inspect_input')}：分析用户上传的数据或自然语言输入，确认数据结构、时间范围、单位、缺失值以及后续输入构建方式。")
        self._emit({"type": "tool_status", "tool_name": "delegate_python_specialist", "status": "running"})
        output = self._run_python_specialist(task)
        self._emit({"type": "tool_result", "tool_name": "delegate_python_specialist", "content": output})
        state["inspected_input_summary"] = output
        self._update_step_state(
            state,
            "inspect_input",
            status="completed",
            title=self._derive_step_title(output, "已完成输入分析"),
            detail="已明确输入分析结论和后续构建方式。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'inspect_input'), output)
        self._emit_reasoning("已完成输入分析，接下来按 skill 明确要求构造标准输入文件。")
        return state

    def _node_prepare_input(self, state: HydroWorkflowState) -> HydroWorkflowState:
        request = state.get("user_request", "")
        case_name = state.get("case_name", "aojiang")
        requirement_summary = state.get("case_requirement_summary", "")
        inspected_input_summary = state.get("inspected_input_summary", "")
        if state.get("input_source_type") == "uploaded_file":
            task = (
                f"请基于已经完成的 skill 规则理解和数据分析结果，严格按照 skill 明确要求的格式构造标准输入文件。"
                f"如需先生成标准中间 Excel，再转换成适用于 {case_name} 案例的 input.json，请完整执行。"
                f"不要跳过数据分析结论，也不要假设文件格式固定。请把中间文件和最终输入文件都输出到会话 outputs 目录，并返回生成文件路径与用途说明。\n\n"
                f"[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}\n\n[数据预览摘要]\n{inspected_input_summary}"
            )
        else:
            task = (
                f"请基于已经完成的 skill 规则理解和需求梳理结果，严格按照 skill 明确要求的格式构造标准输入文件。"
                f"如需先生成标准中间 Excel，再转换成适用于 {case_name} 案例的 input.json，请完整执行。"
                f"请把中间文件和最终输入文件都输出到会话 outputs 目录，并返回生成文件路径与用途说明。\n\n"
                f"[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}\n\n[需求梳理摘要]\n{inspected_input_summary}"
            )
        self._update_step_state(
            state,
            "prepare_input",
            status="running",
            detail="正在构造标准输入文件，并尽量复用中间结果。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'prepare_input')}：根据 skill 要求构造标准输入文件，不跳过格式约束，不凭经验猜测输入结构。")
        self._emit({"type": "tool_status", "tool_name": "delegate_python_specialist", "status": "running"})
        output = self._run_python_specialist(task)
        self._emit({"type": "tool_result", "tool_name": "delegate_python_specialist", "content": output})
        state["intermediate_result"] = output
        self._update_step_state(
            state,
            "prepare_input",
            status="completed",
            title=self._derive_step_title(output, "已完成标准输入构造"),
            detail="标准输入文件已准备完成。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'prepare_input'), output)
        self._emit_reasoning("标准输入文件已准备，接下来执行对应 skill。")
        return state

    def _node_run_model(self, state: HydroWorkflowState) -> HydroWorkflowState:
        request = state.get("user_request", "")
        case_name = state.get("case_name", "aojiang")
        upstream = state.get("intermediate_result", "")
        requirement_summary = state.get("case_requirement_summary", "")
        task = (
            f"请基于下面已经生成的标准输入结果，继续执行 {case_name} 案例对应的 skill。"
            f"请优先复用上游已生成的 input.json，而不是重新整理输入。"
            f"你现在的目标是完成 skill 执行本身，并把结构化结果保存到会话 outputs 目录。\n\n[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}\n\n[上游输入准备结果]\n{upstream}"
        )
        self._update_step_state(
            state,
            "run_model",
            status="running",
            detail="正在执行案例对应的水文 skill。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'run_model')}：执行与当前案例对应的 skill，复用已生成的标准输入文件，产出结构化结果。")
        self._emit({"type": "tool_status", "tool_name": "delegate_python_specialist", "status": "running"})
        output = self._run_python_specialist(task)
        self._emit({"type": "tool_result", "tool_name": "delegate_python_specialist", "content": output})
        state["model_result"] = output
        self._update_step_state(
            state,
            "run_model",
            status="completed",
            title=self._derive_step_title(output, "已完成模型执行"),
            detail="模型执行完成，已生成结构化结果。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'run_model'), output)
        self._emit_reasoning("skill 执行完成，接下来根据当前任务目标整理用户可交付的结果。")
        return state

    def _node_organize_result(self, state: HydroWorkflowState) -> HydroWorkflowState:
        request = state.get("user_request", "")
        case_name = state.get("case_name", "aojiang")
        model_result = state.get("model_result", "")
        requirement_summary = state.get("case_requirement_summary", "")
        script_name = "export_aojiang_hydro_result_to_excel.py" if case_name == "aojiang" else "export_jingzhou_hydro_result_to_excel.py"
        specialist = self._choose_result_specialist(request)
        task = (
            f"请根据下面的 skill 执行结果，整理用户可交付的输出结果。"
            f"如果用户需要 Excel，就优先复用已经生成的结构化结果 JSON，使用 {script_name} 或等价方式生成结果 Excel。"
            f"如果用户需要的是提取后的表格、结果汇总或其他最终结果文件，也在这一步完成整理，输出必须写入会话 outputs 目录。\n\n"
            f"[原始用户需求]\n{request}\n\n[case 约束摘要]\n{requirement_summary}\n\n[模型执行结果]\n{model_result}"
        )
        self._update_step_state(
            state,
            "organize_result",
            status="running",
            detail=f"正在使用 {specialist} specialist 整理最终交付结果。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'organize_result')}：整理 skill 输出结果。根据当前任务目标，自动选择 {specialist} specialist 进行结果整理。")
        if specialist == "excel":
            tool_name = "delegate_excel_specialist"
            self._emit({"type": "tool_status", "tool_name": tool_name, "status": "running"})
            output = self._run_excel_specialist(task)
            self._emit({"type": "tool_result", "tool_name": tool_name, "content": output})
        else:
            tool_name = "delegate_python_specialist"
            self._emit({"type": "tool_status", "tool_name": tool_name, "status": "running"})
            output = self._run_python_specialist(task)
            self._emit({"type": "tool_result", "tool_name": tool_name, "content": output})
        state["organized_result"] = output
        state.setdefault("notes", []).append(f"结果整理阶段自动选择子 agent: {specialist}")
        self._update_step_state(
            state,
            "organize_result",
            status="completed",
            title=self._derive_step_title(output, "已完成结果整理"),
            detail="用户可交付结果已整理完成。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'organize_result'), output)
        self._emit_reasoning("结果整理完成，接下来验证结果是否合理以及用户任务是否已经完成。")
        return state

    def _node_validate_result(self, state: HydroWorkflowState) -> HydroWorkflowState:
        latest = state.get("organized_result") or state.get("model_result") or state.get("intermediate_result") or ""
        task = (
            "请根据原始用户需求检查当前结果是否合理，且是否已经满足最终交付要求。"
            "如果当前只是中间结果或中间文件，请明确指出。\n\n"
            f"[原始用户需求]\n{state.get('user_request', '')}\n\n[最近结果]\n{latest}"
        )
        self._update_step_state(
            state,
            "validate_result",
            status="running",
            detail="正在校验当前结果是否满足用户最终目标。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'validate_result')}：验证结果合理性以及用户最终任务是否已经完成。")
        self._emit({"type": "tool_status", "tool_name": "delegate_validator", "status": "running"})
        output = self._run_validator(task)
        self._emit({"type": "tool_result", "tool_name": "delegate_validator", "content": output})
        parsed = self._extract_validation(output)
        state["validation_result"] = parsed
        self._update_step_state(
            state,
            "validate_result",
            status="completed",
            title=self._derive_validation_title(parsed, output),
            detail="结果校验已完成。",
            outcome=self._derive_validation_outcome(parsed),
        )
        self._emit_stage_result(self._workflow_prefix(state, 'validate_result'), output)
        if parsed.get("overall_status") == "fail":
            state.setdefault("notes", []).append(parsed.get("final_goal") or "validator 判定当前结果未完成最终目标")
            self._emit_reasoning("校验结果显示当前仍未完成最终目标，最终响应中会保留校验提示。")
        else:
            self._emit_reasoning("校验通过，接下来整理最终交付内容与完成情况总结。")
        return state

    def _node_summarize_workflow(self, state: HydroWorkflowState) -> HydroWorkflowState:
        self._update_step_state(
            state,
            "summarize_workflow",
            status="running",
            detail="正在汇总整个流程、已完成工作与最终结果。",
        )
        self._emit_reasoning(f"{self._workflow_prefix(state, 'summarize_workflow')}：汇总整个流程，梳理已完成工作与最终交付结果。")
        self._emit({"type": "tool_status", "tool_name": "delegate_python_specialist", "status": "running"})
        summary_text = self._generate_workflow_summary(state)
        self._emit({"type": "tool_result", "tool_name": "delegate_python_specialist", "content": summary_text})
        state["workflow_summary"] = summary_text
        self._update_step_state(
            state,
            "summarize_workflow",
            status="completed",
            title=self._derive_step_title(summary_text, "已完成全流程总结"),
            detail="工作流总结已生成。",
        )
        self._emit_stage_result(self._workflow_prefix(state, 'summarize_workflow'), summary_text)
        return state

    def _node_finalize_response(self, state: HydroWorkflowState) -> HydroWorkflowState:
        validation = state.get("validation_result") or {}
        workflow_summary = self._extract_stage_summary(state.get("workflow_summary", "")).strip()
        if self._is_low_quality_summary(workflow_summary):
            workflow_summary = self._generate_workflow_summary(state)
            state["workflow_summary"] = workflow_summary

        final_result = (
            workflow_summary
            or self._extract_stage_summary(state.get("organized_result", ""))
            or self._extract_stage_summary(state.get("model_result", ""))
            or self._extract_stage_summary(state.get("intermediate_result", ""))
            or self._generate_workflow_summary(state)
            or "水文工作流未生成可用结果。"
        )

        normalized = final_result.strip()
        if normalized in {"校验状态：pass\n是否完成最终目标：True", "校验状态：pass\n是否完成最终目标：true"}:
            final_result = "任务已完成，预测结果已生成并整理为最终交付文件。"

        if validation and str(validation.get("overall_status", "")).strip().lower() == "fail":
            final_goal = str(validation.get("final_goal", "") or "").strip()
            if final_goal and final_goal not in final_result:
                final_result = f"{final_result}\n\n当前仍未完成最终目标：{final_goal}"

        state["final_answer"] = final_result.strip()
        return state

    def _extract_validation(self, output: str) -> Dict[str, Any]:
        text = (output or "").strip()
        try:
            payload = json.loads(text)
            if isinstance(payload, dict) and "validation" in payload:
                return payload["validation"]
        except Exception:
            pass
        return self._parse_validator_output(text)

    @staticmethod
    def _route_after_classify(state: HydroWorkflowState) -> str:
        return "resolve_case_context" if state.get("need_input_prep") or state.get("need_model_run") else "finalize_response"

    @staticmethod
    def _route_after_inspect(state: HydroWorkflowState) -> str:
        return "prepare_input" if state.get("need_input_prep") else "run_model" if state.get("need_model_run") else "finalize_response"

    @staticmethod
    def _route_after_prepare(state: HydroWorkflowState) -> str:
        return "run_model" if state.get("need_model_run") else "finalize_response"

    @staticmethod
    def _route_after_run_model(state: HydroWorkflowState) -> str:
        return "organize_result" if state.get("need_result_organize") else "validate_result"
