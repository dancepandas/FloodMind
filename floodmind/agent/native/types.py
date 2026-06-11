"""
Native Agent Runtime 核心数据结构

LangChain-free 的 Agent 执行层数据类型定义。
ToolSpec / ToolCall / ToolResult 统一由 agent.runtime.contracts.tools 定义，
此模块 re-export 保持向后兼容。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult, ToolSpec


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
    model_key: str = ""
    enable_reasoning: bool = False
    enable_search: bool = False
    enable_rag: bool = False
    abort_check: Optional[Callable[[], bool]] = None


@dataclass
class ModelEvent:
    type: Literal[
        "reasoning",
        "token",
        "tool_call_delta",
        "tool_call_done",
        "done",
        "usage",
        "error",
        "timeout",
    ]
    content: str = ""
    tool_call: Optional[ToolCall] = None
    raw: Optional[dict] = None


@dataclass
class AgentResult:
    final_output: str
    reasoning: str
    tool_results: List[ToolResult] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    is_timeout: bool = False


@dataclass
class PlanStep:
    id: str
    title: str
    detail: str = ""
    status: Literal["pending", "running", "completed", "error", "skipped"] = "pending"
    tool_name: str = ""
    artifact_ids: List[str] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentLoopState:
    run_id: str
    iteration: int = 0
    plan: Optional['ExecutionPlan'] = None
    current_step_id: str = ""
    completed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    artifact_registry: Dict[str, dict] = field(default_factory=dict)
    execution_journal: List[dict] = field(default_factory=list)
    needs_replan: bool = False
    validation_errors: List[str] = field(default_factory=list)
    original_input: str = ""
    user_message: str = ""
    final_output: str = ""
    latest_payload: Optional[Dict[str, Any]] = None
    artifacts: List[str] = field(default_factory=list)
    round_count: int = 0
    replan_count: int = 0
    terminal_status: str = "running"
    todos: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ArtifactRecord:
    file_name: str
    file_path: str
    kind: Literal["file", "image"]
    source_tool: str
    verified: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    plan_id: str = ""
    user_message: str = ""
    goal_deliverables: List[Dict[str, str]] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    terminal_status: str = "running"

    def find_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("step_id") == step_id:
                return s
        return None

    def next_pending_step(self) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s.get("status") == "pending":
                return s
        return None

    def all_steps_completed(self) -> bool:
        return all(s.get("status") == "completed" for s in self.steps) if self.steps else False

    def topological_sort(self) -> List[str]:
        """Kahn 算法拓扑排序，返回 step_id 列表。有环时返回空列表。"""
        in_degree: Dict[str, int] = {}
        adj: Dict[str, List[str]] = {}
        all_ids = set()

        for s in self.steps:
            sid = s.get("step_id", "")
            if not sid:
                continue
            all_ids.add(sid)
            in_degree.setdefault(sid, 0)
            adj.setdefault(sid, [])

        for s in self.steps:
            sid = s.get("step_id", "")
            needs = s.get("needs", []) or []
            for dep in needs:
                if dep not in all_ids:
                    import logging
                    logging.getLogger(__name__).warning(
                        "步骤 %s 依赖了不存在的步骤 %s，已跳过", sid, dep
                    )
                    continue
                in_degree[sid] = in_degree.get(sid, 0) + 1
                adj.setdefault(dep, []).append(sid)

        queue = [sid for sid in all_ids if in_degree.get(sid, 0) == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result if len(result) == len(all_ids) else []

    def has_cycle(self) -> bool:
        """DFS 三色标记检测循环"""
        WHITE, GRAY, BLACK = 0, 1, 2
        all_ids = {s.get("step_id", "") for s in self.steps if s.get("step_id")}
        adj: Dict[str, List[str]] = {sid: [] for sid in all_ids}
        for s in self.steps:
            sid = s.get("step_id", "")
            needs = s.get("needs", []) or []
            for dep in needs:
                if dep in adj:
                    adj[dep].append(sid)

        color: Dict[str, int] = {sid: WHITE for sid in all_ids}

        def _dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if color[neighbor] == GRAY:
                    return True
                if color[neighbor] == WHITE and _dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for sid in all_ids:
            if color[sid] == WHITE and _dfs(sid):
                return True
        return False

    def get_batches(self) -> List[List[str]]:
        """按拓扑层级分批：每批内的步骤可并行执行"""
        order = self.topological_sort()
        if not order:
            return [[s.get("step_id", "") for s in self.steps]]

        all_ids = set(order)
        # 构建 steps 查找表
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
        return cls(
            plan_id=data.get("plan_id", ""),
            user_message=data.get("user_message", ""),
            goal_deliverables=data.get("goal_deliverables", []),
            steps=data.get("steps", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            terminal_status=data.get("terminal_status", "running"),
        )


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
