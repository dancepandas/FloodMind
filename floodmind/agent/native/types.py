"""
Native Agent Runtime 核心数据结构

LangChain-free 的 Agent 执行层数据类型定义。
ToolSpec / ToolCall / ToolResult 统一由 agent.runtime.contracts.tools 定义，
此模块 re-export 保持向后兼容。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult, ToolSpec


# ── Agent 执行状态枚举 ─────────────────────────────────────────

AgentLoopStatus = Literal[
    "created",
    "awaiting_llm",
    "awaiting_tool",
    "awaiting_permission",
    "context_compress",
    "paused",
    "failed",
    "completed",
]


TerminalReasonCode = Literal[
    "completed",
    "tool_calls",
    "max_tokens",
    "filtered",
    "refused",
    "paused",
    "aborted",
    "error",
    "unknown",
]


@dataclass(frozen=True)
class TerminalReason:
    """Provider-neutral terminal contract while retaining the wire value."""

    code: TerminalReasonCode
    raw: str = ""

    @property
    def is_normal_completion(self) -> bool:
        return self.code == "completed"

    @classmethod
    def from_raw(cls, reason: Any) -> "TerminalReason":
        raw = "" if reason is None else str(reason)
        normalized = raw.strip().lower()
        mapping: Dict[str, TerminalReasonCode] = {
            "stop": "completed",
            "end_turn": "completed",
            "tool_calls": "tool_calls",
            "tool_use": "tool_calls",
            "length": "max_tokens",
            "max_tokens": "max_tokens",
            "content_filter": "filtered",
            "filtered": "filtered",
            "refusal": "refused",
            "refused": "refused",
            "pause_turn": "paused",
            "paused": "paused",
            "abort": "aborted",
            "aborted": "aborted",
            "error": "error",
        }
        return cls(code=mapping.get(normalized, "unknown"), raw=raw)


@dataclass
class Attachment:
    file_id: str
    name: str
    path: str
    kind: Literal["image", "document"]
    mime_type: str
    size: int


@dataclass
class RunContext:
    session_id: str
    user_text: str
    attachments: List[Attachment] = field(default_factory=list)
    output_dir: str = ""
    upload_dir: str = ""
    cwd: str = ""
    workspace_dir: str = ""
    state_dir: str = ""
    artifact_dir: str = ""
    tmp_dir: str = ""
    scripts_dir: str = ""
    model_key: str = ""
    enable_reasoning: bool = False
    enable_search: bool = False
    enable_rag: bool = False
    abort_check: Optional[Callable[[], bool]] = None
    # 子代理专用：主代理委派时指定的工作目录（桌面版并行写 user_dir 子目录的关键接缝）。
    # 主代理不设；子代理默认 cwd 优先用它，其次 sandbox workspace_dir。
    delegate_cwd: str = ""
    # agent 身份（阶段D）：主代理="main"，子代理="sub"。决定权限分层。
    agent_tier: str = "main"
    # 由 RuntimeContext 注入每次运行所需的服务。
    runtime_context: Optional[Any] = None
    # 运行模式（阶段E）：planning=只读硬门，execution=执行。
    # 由 executor 从 AgentLoopState.mode 注入；子代理恒 execution。
    mode: str = "execution"


@dataclass(frozen=True)
class InvalidToolCall:
    """A provider tool request that is safe to journal/return, but never execute."""

    id: str
    name: str
    raw_arguments: str
    error: str


@dataclass
class ModelEvent:
    type: Literal[
        "reasoning",
        "token",
        "tool_call_delta",
        "tool_call_done",
        "invalid_tool_call",
        "assistant_message_done",
        "done",
        "usage",
        "error",
        "timeout",
    ]
    content: str = ""
    tool_call: Optional[ToolCall] = None
    invalid_tool_call: Optional[InvalidToolCall] = None
    terminal_reason: Optional[TerminalReason] = None
    raw: Optional[dict] = None


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AgentResult:
    final_output: str
    reasoning: str
    tool_results: List[ToolResult] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    is_timeout: bool = False


class PlanStepSubtask(TypedDict, total=False):
    """执行步骤内部的细粒度子任务。"""

    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]
    priority: Literal["high", "normal", "low"]


class PlanStepDict(TypedDict, total=False):
    """ExecutionPlan.steps 中单个步骤的字典结构提示。"""

    step_id: str
    title: str
    executor: str
    skill_name: str
    purpose: str
    status: Literal["pending", "running", "completed", "error", "skipped"]
    expected_deliverables: List[Dict[str, str]]
    output_artifacts: List[str]
    output_summary: str
    error_message: str
    attempt_count: int
    needs: List[str]
    subtasks: List[PlanStepSubtask]


class AgentLoopState(BaseModel):
    """Agent 主循环状态机状态。

    使用 Pydantic BaseModel 以支持 checkpoint 序列化/反序列化。
    所有运行时状态集中于此，NativeAgentExecutor 据此驱动状态转移。
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    session_id: str = ""
    run_id: str = ""
    checkpoint_id: str = ""
    status: AgentLoopStatus = "created"
    iteration: int = 0
    attempt_id: str = ""
    max_iterations: int = 10000

    # 对话与执行上下文
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    plan: Optional["ExecutionPlan"] = None
    tool_results: List[ToolResult] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)

    # 运行中间状态
    reasoning: str = ""
    final_output: str = ""
    current_answer: str = ""
    # 当前 LLM 调用轮的 reasoning 切片（本轮产物，写 memory 用；跨轮在 reasoning_before 处切片）
    round_reasoning: str = ""
    # 已并入 state.messages 的用户消息数（用于检测运行中追加的排队指令）
    consumed_user_message_count: int = 0
    pending_tool_calls: List[ToolCall] = Field(default_factory=list)
    pending_ask_id: Optional[str] = None
    terminal_reason: Optional[TerminalReason] = None
    max_token_continuations: int = 1
    max_token_continuation_count: int = 0

    # 防御机制状态
    doom_loop_tracker: List[Tuple[str, str]] = Field(default_factory=list)
    consecutive_failures: Dict[str, int] = Field(default_factory=dict)

    # 输入与元信息
    original_input: str = ""
    user_message: str = ""
    token_usage: TokenUsage = Field(default_factory=TokenUsage)

    # 兼容旧字段（保留，但逐步迁移到新字段）
    artifact_registry: Dict[str, dict] = Field(default_factory=dict)
    execution_journal: List[dict] = Field(default_factory=list)
    # 阶段E：规划/执行模式。planning=只读硬门，execution=执行（默认）。
    # 仅主代理持 mode；子代理恒 execution。
    mode: str = "execution"

    # 时间戳
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def mark_updated(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


class ArtifactRecord(BaseModel):
    file_name: str
    file_path: str
    kind: Literal["file", "image"]
    source_tool: str
    verified: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    plan_id: str = ""
    user_message: str = ""
    goal_deliverables: List[Dict[str, str]] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    terminal_status: str = "running"

    model_config = ConfigDict(extra="allow")

    def find_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("step_id") == step_id:
                return s
        return None

    @staticmethod
    def normalize_needs(value: Any) -> List[str]:
        """Normalize dependency input to an ordered list of non-empty strings."""
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                decoded = [value] if value else []
            value = decoded
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        result: List[str] = []
        for dependency in value:
            dependency_id = str(dependency).strip()
            if dependency_id and dependency_id not in result:
                result.append(dependency_id)
        return result

    @classmethod
    def normalize_steps(cls, raw_steps: Any) -> List[Dict[str, Any]]:
        """Normalize a complete plan and validate its dependency graph."""
        if not isinstance(raw_steps, list):
            raw_steps = [raw_steps]

        normalized: List[Dict[str, Any]] = []
        explicit_ids = [
            str(step.get("step_id") or "").strip()
            for step in raw_steps
            if isinstance(step, dict) and step.get("step_id")
        ]
        if len(explicit_ids) != len(set(explicit_ids)):
            duplicate = next(step_id for step_id in explicit_ids if explicit_ids.count(step_id) > 1)
            raise ValueError(f"步骤 ID 重复: {duplicate}")
        reserved_ids = set(explicit_ids)
        used_ids: set[str] = set()
        for index, raw in enumerate(raw_steps):
            step = dict(raw) if isinstance(raw, dict) else {"title": str(raw)[:60]}
            requested_id = str(step.get("step_id") or "").strip()
            if requested_id:
                step_id = requested_id
            else:
                suffix = index + 1
                step_id = f"step-{suffix}"
                while step_id in used_ids or step_id in reserved_ids:
                    suffix += 1
                    step_id = f"step-{suffix}"
            used_ids.add(step_id)
            step["step_id"] = step_id
            step["status"] = "pending"
            step["needs"] = cls.normalize_needs(step.get("needs"))
            normalized.append(step)

        plan = cls(steps=normalized)
        plan.validate_dependencies()
        return normalized

    def validate_dependencies(self) -> None:
        """Reject missing, self-referential, or cyclic dependencies."""
        ids = [str(step.get("step_id") or "").strip() for step in self.steps]
        if any(not step_id for step_id in ids):
            raise ValueError("计划步骤缺少 step_id")
        if len(ids) != len(set(ids)):
            raise ValueError("计划步骤 ID 必须唯一")
        known_ids = set(ids)
        for step in self.steps:
            step_id = str(step["step_id"])
            needs = self.normalize_needs(step.get("needs"))
            step["needs"] = needs
            if step_id in needs:
                raise ValueError(f"步骤 {step_id} 不能依赖自身")
            missing = [dependency for dependency in needs if dependency not in known_ids]
            if missing:
                raise ValueError(f"步骤 {step_id} 依赖不存在的步骤: {', '.join(missing)}")
        if self.has_cycle():
            raise ValueError("执行计划存在依赖环")

    def replace_needs(self, step_id: str, needs: Any) -> None:
        """Validate and apply a dependency change atomically."""
        target = self.find_step(step_id)
        if target is None:
            raise ValueError(f"步骤 {step_id} 不存在")
        previous = target.get("needs", [])
        target["needs"] = self.normalize_needs(needs)
        try:
            self.validate_dependencies()
        except ValueError:
            target["needs"] = previous
            raise

    def next_pending_step(self) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("status") == "pending":
                return s
        return None

    def all_steps_completed(self) -> bool:
        return all(s.get("status") == "completed" for s in self.steps) if self.steps else False

    def _topological_order(self) -> tuple[List[str], Dict[str, List[str]]]:
        """Build the dependency graph once and return its stable Kahn ordering."""
        step_ids = [
            str(step.get("step_id"))
            for step in self.steps
            if step.get("step_id")
        ]
        known_ids = set(step_ids)
        in_degree = {step_id: 0 for step_id in step_ids}
        dependents: Dict[str, List[str]] = {step_id: [] for step_id in step_ids}

        for step in self.steps:
            step_id = str(step.get("step_id") or "")
            if step_id not in known_ids:
                continue
            for dependency in self.normalize_needs(step.get("needs")):
                if dependency not in known_ids:
                    import logging
                    logging.getLogger(__name__).warning(
                        "步骤 %s 依赖了不存在的步骤 %s，已跳过", step_id, dependency
                    )
                    continue
                in_degree[step_id] += 1
                dependents[dependency].append(step_id)

        queue = [step_id for step_id in step_ids if in_degree[step_id] == 0]
        order: List[str] = []
        cursor = 0
        while cursor < len(queue):
            node = queue[cursor]
            cursor += 1
            order.append(node)
            for dependent in dependents[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        return order, dependents

    def topological_sort(self) -> List[str]:
        """Kahn 算法拓扑排序，返回 step_id 列表。有环时返回空列表。"""
        order, graph = self._topological_order()
        return order if len(order) == len(graph) else []

    def has_cycle(self) -> bool:
        """Return whether the shared dependency graph contains a cycle."""
        order, graph = self._topological_order()
        return len(order) != len(graph)

    def get_batches(self) -> List[List[str]]:
        """按拓扑层级分批：每批内的步骤可并行执行"""
        order = self.topological_sort()
        if not order:
            if self.steps:
                raise ValueError("执行计划存在依赖环或无效依赖，无法分批执行")
            return []

        all_ids = set(order)
        step_map = {s.get("step_id", ""): s for s in self.steps}

        depth: Dict[str, int] = {}
        for sid in order:
            s = step_map.get(sid, {})
            needs = s.get("needs", []) or []
            depth[sid] = max((depth.get(dep, 0) for dep in needs if dep in all_ids), default=-1) + 1

        batches: Dict[int, List[str]] = {}
        for sid, d in depth.items():
            batches.setdefault(d, []).append(sid)

        return [batches[d] for d in sorted(batches.keys())]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        return cls.model_validate(data)


# ── Part 类型定义（对齐 OpenCode MessageV2.Part 体系）────────────────

@dataclass
class StepStartPart:
    """LLM Step 开始（对应 OpenCode StepStartPart）"""
    type: str = "step_start"
    id: str = ""
    snapshot: Optional[str] = None
    timestamp: float = 0.0


@dataclass
class StepFinishPart:
    """LLM Step 结束（对应 OpenCode StepFinishPart）"""
    type: str = "step_finish"
    id: str = ""
    reason: str = ""
    cost: float = 0.0
    tokens: Dict[str, int] = field(default_factory=dict)
    snapshot: Optional[str] = None
    timestamp: float = 0.0


@dataclass
class PatchPart:
    """文件变更记录（对应 OpenCode PatchPart）"""
    type: str = "patch"
    id: str = ""
    hash: str = ""
    files: List[str] = field(default_factory=list)
    timestamp: float = 0.0
