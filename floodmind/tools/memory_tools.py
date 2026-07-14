"""
Memory 工具模块

提供 Agent 主动查询记忆的能力：
- ConversationSearch: 搜索当前会话历史
- ExperienceSearch: 搜索经验树
- CoreMemoryAppend: 追加关键事实到 core memory
- CoreMemoryRead: 读取 core memory
- JournalSearch: 搜索 execution journal
- JournalGetFullResult: 读取 journal 归档的完整工具结果

所有工具都遵循 build_agent_tool 模式，与现有工具体系统一。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy
from floodmind.agent.runtime.services.execution_journal_service import ExecutionJournalService
from floodmind.memory.task_experience import get_task_experience_store
from floodmind.tools.agent_tool import (
    ToolRegistry,
    build_agent_tool,
    make_readonly_permission_fn,
    make_write_permission_fn,
)
from floodmind.tools.base_tools import (
    SESSION_CONTEXT,
    _finalize_tool_output,
    _parse_json_if_needed,
    get_memory_instance,
)
from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT as _PROJECT_ROOT

logger = logging.getLogger(__name__)


def _get_session_id() -> str:
    return SESSION_CONTEXT.get("session_id", "") or "default"


def _get_session_dir(session_id: str) -> Path:
    return _PROJECT_ROOT / "data" / "sessions" / session_id


def _core_memory_path(session_id: str) -> Path:
    output_dir = SESSION_CONTEXT.get("output_dir")
    if output_dir:
        return Path(output_dir).parent / "core_memory.json"
    return _PROJECT_ROOT / "data" / "sessions" / session_id / "core_memory.json"


# ── ConversationSearch ─────────────────────────────────────────────────────

class ConversationSearchInput(BaseModel):
    query: str = Field(description="[必填] 搜索关键词")
    top_k: int = Field(default=3, description="[可选] 返回最近相关的轮次数")


def _impl_conversation_search(query: str = "", top_k: int = 3) -> str:
    query = str(query).strip().strip('"').strip("'")
    if not query:
        return _finalize_tool_output("conversation_search", "错误：query 不能为空", query=query, top_k=top_k)

    memory_instance = get_memory_instance()
    if memory_instance is None:
        return _finalize_tool_output("conversation_search", "错误：记忆系统未初始化", query=query, top_k=top_k)

    try:
        if hasattr(memory_instance, "search_history"):
            results = memory_instance.search_history(query, top_k)
            if not results or "未找到" in results:
                return _finalize_tool_output("conversation_search", f"未找到与 '{query}' 相关的对话", query=query, top_k=top_k)
            return _finalize_tool_output("conversation_search", results, query=query, top_k=top_k)
        else:
            return _finalize_tool_output("conversation_search", "错误：记忆系统不支持搜索", query=query, top_k=top_k)
    except Exception as e:
        logger.error("搜索对话历史失败: %s", e)
        return _finalize_tool_output("conversation_search", f"搜索失败: {str(e)}", query=query, top_k=top_k)


conversation_search = build_agent_tool(
    name="ConversationSearch",
    description="在当前会话历史中搜索与 query 相关的对话轮次。当你需要回顾之前说过什么、做过什么时使用。",
    args_schema=ConversationSearchInput,
    func=_impl_conversation_search,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


# ── ExperienceSearch ───────────────────────────────────────────────────────

class ExperienceSearchInput(BaseModel):
    query: str = Field(description="[必填] 搜索关键词")
    top_k: int = Field(default=3, description="[可选] 返回最多几条经验")


def _impl_experience_search(query: str = "", top_k: int = 3) -> str:
    query = str(query).strip().strip('"').strip("'")
    if not query:
        return _finalize_tool_output("experience_search", "错误：query 不能为空", query=query, top_k=top_k)

    try:
        store = get_task_experience_store()
        leaves = store.search_keywords(query, top_k=top_k)
        if not leaves:
            return _finalize_tool_output("experience_search", f"未找到与 '{query}' 相关的经验", query=query, top_k=top_k)
        results = store.render_experience_markdown(leaves)
        return _finalize_tool_output("experience_search", results, query=query, top_k=top_k)
    except Exception as e:
        logger.error("搜索经验失败: %s", e)
        return _finalize_tool_output("experience_search", f"搜索失败: {str(e)}", query=query, top_k=top_k)


experience_search = build_agent_tool(
    name="ExperienceSearch",
    description="在历史任务经验树中搜索与当前任务相关的经验。开始新任务前可先调用，避免重复踩坑。",
    args_schema=ExperienceSearchInput,
    func=_impl_experience_search,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


# ── CoreMemoryAppend / CoreMemoryRead ──────────────────────────────────────

class CoreMemoryAppendInput(BaseModel):
    category: str = Field(description="[必填] 事实类别，如 user_preferences, project_constraints, task_state")
    fact: str = Field(description="[必填] 要记录的关键事实")


class CoreMemoryReadInput(BaseModel):
    category: Optional[str] = Field(default=None, description="[可选] 只读取某个类别，不填则读取全部")


def _load_core_memory(session_id: str) -> Dict[str, Any]:
    path = _core_memory_path(session_id)
    if not path.exists():
        return {"updated_at": datetime.now(timezone.utc).isoformat(), "facts": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 core memory 失败: %s", e)
        return {"updated_at": datetime.now(timezone.utc).isoformat(), "facts": {}}


def _save_core_memory(session_id: str, data: Dict[str, Any]) -> None:
    path = _core_memory_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _impl_core_memory_append(category: str = "", fact: str = "") -> str:
    category = str(category).strip().strip('"').strip("'")
    fact = str(fact).strip().strip('"').strip("'")
    if not category:
        return _finalize_tool_output("core_memory_append", "错误：category 不能为空", category=category, fact=fact)
    if not fact:
        return _finalize_tool_output("core_memory_append", "错误：fact 不能为空", category=category, fact=fact)

    session_id = _get_session_id()
    data = _load_core_memory(session_id)
    facts = data.setdefault("facts", {})
    category_facts = facts.setdefault(category, [])

    if fact not in category_facts:
        category_facts.append(fact)
        _save_core_memory(session_id, data)
        return _finalize_tool_output("core_memory_append", f"已记录到 core memory [{category}]: {fact}", category=category, fact=fact)
    return _finalize_tool_output("core_memory_append", f"该事实已存在于 [{category}]，未重复追加", category=category, fact=fact)


core_memory_append = build_agent_tool(
    name="CoreMemoryAppend",
    description="把关键事实追加到 core memory，跨轮次保持可见。用于固化用户偏好、项目约束、任务状态等。",
    args_schema=CoreMemoryAppendInput,
    func=_impl_core_memory_append,
    is_readonly=False,
    is_destructive=False,
    is_concurrency_safe=False,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="state_write"),
)


def _impl_core_memory_read(category: Optional[str] = None) -> str:
    session_id = _get_session_id()
    data = _load_core_memory(session_id)
    facts = data.get("facts", {})

    if category:
        category = str(category).strip().strip('"').strip("'")
        items = facts.get(category, [])
        if not items:
            return _finalize_tool_output("core_memory_read", f"[{category}] 下没有记录", category=category)
        content = f"## Core Memory: {category}\n" + "\n".join(f"- {item}" for item in items)
        return _finalize_tool_output("core_memory_read", content, category=category)

    if not facts:
        return _finalize_tool_output("core_memory_read", "core memory 为空")

    parts = ["# Core Memory"]
    for cat, items in facts.items():
        parts.append(f"\n## {cat}")
        for item in items:
            parts.append(f"- {item}")
    return _finalize_tool_output("core_memory_read", "\n".join(parts))


core_memory_read = build_agent_tool(
    name="CoreMemoryRead",
    description="读取 core memory 中的关键事实。开始新任务前或遗忘关键约束时可调用。",
    args_schema=CoreMemoryReadInput,
    func=_impl_core_memory_read,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


# ── JournalSearch / JournalGetFullResult ───────────────────────────────────

class JournalSearchInput(BaseModel):
    query: str = Field(description="[必填] 搜索关键词")
    top_k: int = Field(default=3, description="[可选] 返回最多几条结果")


class JournalGetFullResultInput(BaseModel):
    ref_id: str = Field(description="[必填] journal 归档引用 ID")


def _impl_journal_search(query: str = "", top_k: int = 3) -> str:
    query = str(query).strip().strip('"').strip("'")
    if not query:
        return _finalize_tool_output("journal_search", "错误：query 不能为空", query=query, top_k=top_k)

    session_id = _get_session_id()
    try:
        svc = ExecutionJournalService()
        entries = svc.get_recent_summaries(session_id, n=100)
        logger.info("[JournalSearch] session_id=%s, turns=%d, query=%s", session_id, len(entries), query)
        if not entries:
            return _finalize_tool_output("journal_search", "当前会话没有 journal 记录", query=query, top_k=top_k)

        query_words = [w.lower() for w in query.split() if len(w) > 1]
        candidates = []
        for entry in entries:
            score = 0
            text_parts = []
            text_parts.append(entry.llm.answer_fragment.lower())
            for tr in entry.tool_results:
                text_parts.append(tr.tool_name.lower())
                text_parts.append(tr.summary.lower())
            text = " ".join(text_parts)
            for word in query_words:
                if word in text:
                    score += 1
            if score > 0:
                candidates.append((score, entry))

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[:top_k]

        if not selected:
            return _finalize_tool_output("journal_search", f"未找到与 '{query}' 相关的 journal 记录", query=query, top_k=top_k)

        parts = [f"# Journal 搜索结果（query: {query}）"]
        for score, entry in selected:
            parts.append(f"\n## Turn {entry.turn_index} (score={score})")
            parts.append(f"LLM: {entry.llm.answer_fragment}")
            for tr in entry.tool_results:
                parts.append(f"- Tool {tr.tool_name}: {tr.summary}")
                if tr.full_ref:
                    parts.append(f"  完整结果: {tr.full_ref}")
        return _finalize_tool_output("journal_search", "\n".join(parts), query=query, top_k=top_k)
    except Exception as e:
        logger.error("搜索 journal 失败: %s", e)
        return _finalize_tool_output("journal_search", f"搜索失败: {str(e)}", query=query, top_k=top_k)


journal_search = build_agent_tool(
    name="JournalSearch",
    description="在执行日志中搜索与 query 相关的工具调用和结果。当摘要不够详细、想追溯某步执行过程时使用。",
    args_schema=JournalSearchInput,
    func=_impl_journal_search,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)


def _impl_journal_get_full_result(ref_id: str = "") -> str:
    ref_id = str(ref_id).strip().strip('"').strip("'")
    # 兜底：agent 常从“已归档到 journal/full_results/{ref_id}.json”提示里把带 .json 后缀的
    # 整串（甚至整条相对路径）当 ref_id 传入。剥掉后缀/路径，只留 ref_id 本体。
    if "/" in ref_id or "\\" in ref_id:
        ref_id = ref_id.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if ref_id.endswith(".json"):
        ref_id = ref_id[:-len(".json")]
    if not ref_id:
        return _finalize_tool_output("journal_get_full_result", "错误：ref_id 不能为空", ref_id=ref_id)

    session_id = _get_session_id()
    try:
        svc = ExecutionJournalService()
        archived = svc.get_full_result(session_id, ref_id)
        logger.info("[JournalGetFullResult] session_id=%s, ref_id=%s, found=%s", session_id, ref_id, archived is not None)
        if archived is None:
            return _finalize_tool_output("journal_get_full_result", f"未找到归档结果: {ref_id}", ref_id=ref_id)
        return _finalize_tool_output(
            "journal_get_full_result",
            f"# 完整工具结果\nTool: {archived.tool_name}\nStatus: {archived.status}\n\n{archived.content}",
            ref_id=ref_id,
        )
    except Exception as e:
        logger.error("读取 journal 完整结果失败: %s", e)
        return _finalize_tool_output("journal_get_full_result", f"读取失败: {str(e)}", ref_id=ref_id)


journal_get_full_result = build_agent_tool(
    name="JournalGetFullResult",
    description="读取 journal 中归档的完整工具结果。需要传入 JournalSearch 返回的 full_ref。",
    args_schema=JournalGetFullResultInput,
    func=_impl_journal_get_full_result,
    is_readonly=True,
    is_destructive=False,
    is_concurrency_safe=True,
    check_permissions_fn=make_readonly_permission_fn(),
    permission_policy=ToolPermissionPolicy(policy_type="readonly"),
)
