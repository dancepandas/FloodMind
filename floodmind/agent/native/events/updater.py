"""
Pure function state updater — OpenCode-style immutable state transitions.

apply_event(state, event) -> new_state

Rules:
- No side effects
- State is never mutated in-place; always return a new object
- Events are the single source of truth
"""

import copy
import logging
from typing import Any, Dict, List, Optional

from floodmind.agent.native.types import AgentLoopState, ExecutionPlan
from .schema import SessionEvent

logger = logging.getLogger(__name__)


def evolve(state: AgentLoopState, **changes) -> AgentLoopState:
    """Create a new AgentLoopState with the given field changes."""
    # Use dataclasses.replace for shallow copy + field override
    from dataclasses import replace
    return replace(state, **changes)


def _update_plan_step(plan: Optional[ExecutionPlan], payload: dict) -> Optional[ExecutionPlan]:
    """Update a single step in the plan and return a new plan object."""
    if plan is None:
        return None

    step_id = payload.get("step_id", "")
    new_status = payload.get("status", "")

    # Deep-copy steps list
    new_steps = [copy.deepcopy(s) for s in plan.steps]
    for s in new_steps:
        if s.get("step_id") == step_id:
            s["status"] = new_status
            if "error_message" in payload:
                s["error_message"] = payload["error_message"]
            if "output_summary" in payload:
                s["output_summary"] = payload["output_summary"]
            if "output_artifacts" in payload:
                s["output_artifacts"] = payload["output_artifacts"]
            if "attempt_count" in payload:
                s["attempt_count"] = s.get("attempt_count", 0) + 1
            break

    return ExecutionPlan(
        plan_id=plan.plan_id,
        user_message=plan.user_message,
        goal_deliverables=copy.deepcopy(plan.goal_deliverables),
        steps=new_steps,
        created_at=plan.created_at,
        updated_at=payload.get("timestamp", plan.updated_at),
        terminal_status=plan.terminal_status,
    )


def apply_event(state: AgentLoopState, event: SessionEvent) -> AgentLoopState:
    """Pure function: apply a single SessionEvent to AgentLoopState.

    Returns a NEW AgentLoopState; the input state is never modified.
    """
    p = event.payload

    if event.type == "step.started":
        return evolve(
            state,
            iteration=state.iteration + 1,
            current_step_id=p.get("step_id", ""),
            terminal_status="running",
        )

    elif event.type == "step.ended":
        # Append to execution journal
        journal_entry = {
            "type": "llm_response",
            "step": state.iteration,
            "has_tool_calls": p.get("has_tool_calls", False),
            "usage": p.get("usage", {}),
            "timestamp": event.timestamp,
        }
        new_journal = list(state.execution_journal)
        new_journal.append(journal_entry)
        return evolve(state, execution_journal=new_journal)

    elif event.type == "step.failed":
        new_journal = list(state.execution_journal)
        new_journal.append({
            "type": "llm_error",
            "step": state.iteration,
            "error": p.get("error", ""),
            "timestamp": event.timestamp,
        })
        return evolve(
            state,
            execution_journal=new_journal,
            terminal_status="error",
        )

    elif event.type == "tool.called":
        new_journal = list(state.execution_journal)
        new_journal.append({
            "type": "tool_call",
            "step": state.iteration,
            "tool_name": p.get("tool_name", ""),
            "call_id": p.get("call_id", ""),
            "timestamp": event.timestamp,
        })
        return evolve(state, execution_journal=new_journal)

    elif event.type == "tool.result":
        # Record successful tool result
        new_tool_results = list(state.tool_results)
        from floodmind.agent.runtime.contracts.tools import ToolResult
        tr = ToolResult(
            tool_call_id=p.get("call_id", ""),
            name=p.get("tool_name", ""),
            content=p.get("content", ""),
            status="completed",
            artifacts=p.get("artifacts", []),
        )
        new_tool_results.append(tr)

        # Update artifact registry
        new_artifact_registry = dict(state.artifact_registry)
        for art in p.get("artifacts", []):
            if isinstance(art, str):
                new_artifact_registry[art] = {
                    "source_tool": p.get("tool_name", ""),
                    "timestamp": event.timestamp,
                }

        return evolve(
            state,
            tool_results=new_tool_results,
            artifact_registry=new_artifact_registry,
        )

    elif event.type == "tool.error":
        new_tool_results = list(state.tool_results)
        from floodmind.agent.runtime.contracts.tools import ToolResult
        tr = ToolResult(
            tool_call_id=p.get("call_id", ""),
            name=p.get("tool_name", ""),
            content=p.get("error", ""),
            status="error",
        )
        new_tool_results.append(tr)

        new_failed = list(state.failed_steps)
        if state.current_step_id and state.current_step_id not in new_failed:
            new_failed.append(state.current_step_id)

        return evolve(
            state,
            tool_results=new_tool_results,
            failed_steps=new_failed,
        )

    elif event.type == "plan.created":
        plan = ExecutionPlan.from_dict(p.get("plan", {}))
        return evolve(state, plan=plan)

    elif event.type == "plan.step.updated":
        new_plan = _update_plan_step(state.plan, p)
        return evolve(state, plan=new_plan)

    elif event.type == "compaction.done":
        new_journal = list(state.execution_journal)
        new_journal.append({
            "type": "compaction",
            "saved_tokens": p.get("saved_tokens", 0),
            "timestamp": event.timestamp,
        })
        return evolve(state, execution_journal=new_journal)

    elif event.type == "agent.role_changed":
        new_journal = list(state.execution_journal)
        new_journal.append({
            "type": "role_change",
            "from": p.get("from_role", ""),
            "to": p.get("to_role", ""),
            "reason": p.get("reason", ""),
            "timestamp": event.timestamp,
        })
        return evolve(state, execution_journal=new_journal)

    elif event.type == "todo.updated":
        return evolve(state, todos=p.get("todos", []))

    # Unknown event type — no-op (defensive)
    logger.warning("apply_event: unknown event type %s", event.type)
    return state


def replay_events(initial_state: AgentLoopState, events: List[SessionEvent]) -> AgentLoopState:
    """Replay a list of events from an initial state.

    This is the core mechanism for state recovery / resume.
    """
    state = initial_state
    for event in sorted(events, key=lambda e: e.timestamp):
        state = apply_event(state, event)
    return state
