"""
ExecutionJournalService — 结构化执行日志服务

把 Agent 每轮执行事件（LLM 决策、工具调用、工具结果）结构化归档到 JSONL。
长工具结果不进入 prompt，只保留摘要 + 引用，完整内容可查询。

设计原则：
- 与业务逻辑解耦，只负责日志归档和摘要
- JSONL append-only，适合流式写入
- 长结果阈值可配置（默认 1000 字符）
- 子代理有独立 session_id，journal 独立保存
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from floodmind.agent.runtime.contracts.journal import (
    ArchivedToolResult,
    LLMCallJournalEntry,
    ToolResultJournalEntry,
    TurnJournalEntry,
)
from floodmind.agent.runtime.contracts.tools import ToolCall, ToolResult

logger = logging.getLogger(__name__)

# 默认长结果归档阈值（字符数）
_DEFAULT_INLINE_THRESHOLD = 1000

# 摘要长度限制
_SUMMARY_MAX_LENGTH = 800


# 默认 turns.jsonl 单文件大小上限（10 MB），超过后滚动归档
_DEFAULT_MAX_TURNS_FILE_SIZE = 10 * 1024 * 1024


class ExecutionJournalService:
    """Agent 执行日志服务。"""

    def __init__(self, base_dir: Optional[str] = None, inline_threshold: int = _DEFAULT_INLINE_THRESHOLD, max_turns_file_size_bytes: int = _DEFAULT_MAX_TURNS_FILE_SIZE):
        """
        Args:
            base_dir: journal 根目录。默认使用当前工作目录下的 data/sessions。
            inline_threshold: 超过此字符数的工具结果会被归档，prompt 中只放摘要。
            max_turns_file_size_bytes: turns.jsonl 单文件大小上限，超过则滚动为 turns.jsonl.1。
        """
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            self._base_dir = Path.cwd() / "data" / "sessions"
        if inline_threshold < 100:
            logger.warning(
                "ExecutionJournalService: inline_threshold=%d 小于最小值 100，已调整为 100",
                inline_threshold,
            )
        self._inline_threshold = max(inline_threshold, 100)
        self._max_turns_file_size_bytes = max(max_turns_file_size_bytes, 1024)

    # ── 公开 API ───────────────────────────────────────────────

    def process_tool_result(
        self,
        session_id: str,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> Tuple[str, ToolResultJournalEntry]:
        """处理一个工具结果，决定是 inline 还是归档。

        Returns:
            (inline_content, journal_entry)
        """
        content = tool_result.content or ""
        if len(content) <= self._inline_threshold:
            entry = ToolResultJournalEntry(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                status=tool_result.status,
                summary=content,
                artifacts=tool_result.artifacts or [],
                inline=True,
            )
            return content, entry

        # 长结果：归档 + 生成摘要
        ref_id = self.archive_tool_result(session_id, tool_call, tool_result)
        summary = self.summarize_tool_result(tool_result)
        entry = ToolResultJournalEntry(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            status=tool_result.status,
            summary=summary,
            full_ref=ref_id,
            artifacts=tool_result.artifacts or [],
            inline=False,
        )
        inline_content = (
            f"[{tool_call.name}] 执行完成，结果已归档到 journal/full_results/{ref_id}.json\n"
            f"摘要: {summary}"
        )
        return inline_content, entry

    def archive_tool_result(
        self,
        session_id: str,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> str:
        """把完整工具结果归档到文件，返回 ref_id。"""
        ref_id = f"{tool_call.name}_{tool_call.id}_{uuid.uuid4().hex[:8]}"
        archived = ArchivedToolResult(
            ref_id=ref_id,
            session_id=session_id,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            status=tool_result.status,
            content=tool_result.content or "",
            artifacts=tool_result.artifacts or [],
            archived_at=datetime.now(timezone.utc),
            metadata=getattr(tool_result, "metadata", {}) or {},
        )

        full_path = self._full_result_path(session_id, ref_id)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(archived.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("ExecutionJournalService: archived tool result %s for session %s", ref_id, session_id)
        return ref_id

    def summarize_tool_result(self, tool_result: ToolResult) -> str:
        """为长工具结果生成简短摘要。"""
        content = tool_result.content or ""
        name = tool_result.name

        if name in {"Read", "Edit", "ApplyPatch", "Write"}:
            return self._summarize_file_content(content)
        if name in {"Bash", "Exec", "RunCommand"}:
            return self._summarize_bash_output(content)
        if name in {"Grep", "Glob", "Search"}:
            return self._summarize_search_output(content)

        # 默认摘要
        if len(content) <= _SUMMARY_MAX_LENGTH:
            return content
        return content[:_SUMMARY_MAX_LENGTH] + f"\n...（共 {len(content)} 字符，已归档）"

    def record_turn(
        self,
        session_id: str,
        turn_index: int,
        checkpoint_id: Optional[str],
        current_answer: str,
        tool_calls: List[ToolCall],
        tool_result_entries: List[ToolResultJournalEntry],
        token_usage: Optional[Dict[str, int]] = None,
    ) -> None:
        """把一轮执行事件追加写入 turns.jsonl。"""
        self._journal_dir(session_id).mkdir(parents=True, exist_ok=True)

        llm_entry = LLMCallJournalEntry(
            answer_fragment=current_answer[:500],
            tool_calls=[
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments_summary": self._summarize_arguments(tc.arguments),
                }
                for tc in tool_calls
            ],
        )

        entry = TurnJournalEntry(
            turn_index=turn_index,
            checkpoint_id=checkpoint_id,
            timestamp=datetime.now(timezone.utc),
            llm=llm_entry,
            tool_results=tool_result_entries,
            token_usage=token_usage or {},
        )

        turns_path = self._turns_path(session_id)
        with turns_path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

        # 单文件大小超过阈值时滚动归档（保留全部历史，编号递增）
        try:
            if turns_path.stat().st_size > self._max_turns_file_size_bytes:
                self._rotate_turns(session_id)
        except Exception as e:
            logger.warning("ExecutionJournalService: 滚动 turns.jsonl 失败: %s", e)

        logger.debug("ExecutionJournalService: recorded turn %d for session %s", turn_index, session_id)

    def get_full_result(self, session_id: str, ref_id: str) -> Optional[ArchivedToolResult]:
        """读取归档的完整工具结果。"""
        full_path = self._full_result_path(session_id, ref_id)
        if not full_path.exists():
            return None
        try:
            return ArchivedToolResult.model_validate_json(full_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("ExecutionJournalService: failed to load archived result %s: %s", ref_id, e)
            return None

    def get_recent_summaries(self, session_id: str, n: int = 5) -> List[TurnJournalEntry]:
        """读取最近 N 轮 journal 摘要（按归档时间顺序聚合后取最后 N 条）。"""
        entries: List[TurnJournalEntry] = []
        turns_path = self._turns_path(session_id)
        # 老归档在前，当前文件在最后，保证 chronological order
        backup_paths = list(reversed(self._list_turns_backups(session_id)))

        for path in backup_paths + [turns_path]:
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(TurnJournalEntry.model_validate_json(line))
                        except Exception as e:
                            logger.warning("ExecutionJournalService: invalid journal line: %s", e)
            except Exception as e:
                logger.warning("ExecutionJournalService: failed to read journal %s: %s", path, e)

        return entries[-n:] if n > 0 else entries

    # ── 内部辅助 ───────────────────────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id

    def _journal_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "journal"

    def _full_results_dir(self, session_id: str) -> Path:
        return self._journal_dir(session_id) / "full_results"

    def _turns_path(self, session_id: str) -> Path:
        return self._journal_dir(session_id) / "turns.jsonl"

    def _list_turns_backups(self, session_id: str) -> List[Path]:
        """返回按编号从大到小排序的归档文件路径（新的归档在前）。"""
        journal_dir = self._journal_dir(session_id)
        if not journal_dir.exists():
            return []
        backups = []
        for p in journal_dir.iterdir():
            name = p.name
            if name.startswith("turns.jsonl.") and name[len("turns.jsonl."):].isdigit():
                backups.append(p)
        backups.sort(key=lambda p: int(p.name.split(".")[-1]), reverse=True)
        return backups

    def _rotate_turns(self, session_id: str) -> None:
        """将当前 turns.jsonl 滚动为编号递增的归档文件。"""
        turns_path = self._turns_path(session_id)
        backups = self._list_turns_backups(session_id)
        next_num = 1
        if backups:
            next_num = int(backups[0].name.split(".")[-1]) + 1
        backup_path = turns_path.with_suffix(f".jsonl.{next_num}")
        turns_path.rename(backup_path)
        logger.info(
            "ExecutionJournalService: turns.jsonl 超过 %d bytes，已滚动到 %s",
            self._max_turns_file_size_bytes,
            backup_path,
        )

    def _full_result_path(self, session_id: str, ref_id: str) -> Path:
        return self._full_results_dir(session_id) / f"{ref_id}.json"

    @staticmethod
    def _summarize_arguments(arguments: Dict[str, Any]) -> str:
        s = json.dumps(arguments, ensure_ascii=False)
        if len(s) <= 200:
            return s
        return s[:200] + "..."

    @staticmethod
    def _summarize_file_content(content: str) -> str:
        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)

        head_lines = lines[:15]
        tail_lines = lines[-10:] if total_lines > 25 else []

        parts = [f"文件内容共 {total_lines} 行 / {total_chars} 字符"]
        if head_lines:
            parts.append("--- 开头 ---")
            parts.extend(head_lines)
        if tail_lines:
            parts.append("--- 结尾 ---")
            parts.extend(tail_lines)

        summary = "\n".join(parts)
        if len(summary) > _SUMMARY_MAX_LENGTH:
            summary = summary[:_SUMMARY_MAX_LENGTH] + "\n...（已截断，完整内容已归档）"
        return summary

    @staticmethod
    def _summarize_bash_output(content: str) -> str:
        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)

        head = "\n".join(lines[:20])
        summary = f"命令输出共 {total_lines} 行 / {total_chars} 字符\n{head}"
        if len(summary) > _SUMMARY_MAX_LENGTH:
            summary = summary[:_SUMMARY_MAX_LENGTH] + "\n...（已截断，完整输出已归档）"
        return summary

    @staticmethod
    def _summarize_search_output(content: str) -> str:
        lines = [l for l in content.splitlines() if l.strip()]
        total = len(lines)
        head = lines[:10]
        summary = f"搜索结果共 {total} 条\n" + "\n".join(head)
        if len(summary) > _SUMMARY_MAX_LENGTH:
            summary = summary[:_SUMMARY_MAX_LENGTH] + "\n...（已截断，完整结果已归档）"
        return summary
