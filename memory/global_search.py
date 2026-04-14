"""
全局搜索服务

提供基于正则表达式的全局内容检索能力：
- 搜索完整对话历史
- 搜索 Skills 文档
- 支持多种数据源扩展

触发方式：
1. 用户显式触发：用户说"帮我找一下..."、"搜索..."
2. 模型自动触发：当上下文不够时调用此工具
"""

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"


@dataclass
class SearchResult:
    """搜索结果"""
    source: str
    source_type: str
    title: str
    matched_content: str
    context_before: str = ""
    context_after: str = ""
    timestamp: str = ""
    relevance_score: float = 0.0
    line_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "title": self.title,
            "matched_content": self.matched_content,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "timestamp": self.timestamp,
            "line_number": self.line_number,
        }

    def to_markdown(self, max_context_chars: int = 200) -> str:
        """转换为 Markdown 格式"""
        lines = [
            f"**来源**: {self.source_type} - {self.title}",
        ]
        if self.timestamp:
            lines.append(f"**时间**: {self.timestamp}")
        if self.line_number:
            lines.append(f"**位置**: 第 {self.line_number} 行")

        lines.append("")
        if self.context_before:
            cb = self.context_before[-max_context_chars:] if len(self.context_before) > max_context_chars else self.context_before
            lines.append(f"```\n...{cb}")
        lines.append(f">>> {self.matched_content} <<<")
        if self.context_after:
            ca = self.context_after[:max_context_chars] if len(self.context_after) > max_context_chars else self.context_after
            lines.append(f"{ca}...\n```")

        return "\n".join(lines)


class GlobalSearch:
    """
    全局搜索服务

    支持的数据源：
    - chat_history: 完整对话历史
    - skills: Skills 文档
    - 可扩展：memory、uploads 等
    """

    DEFAULT_CONFIG = {
        "max_results_per_source": 10,
        "max_context_chars": 150,
        "fuzzy_threshold": 0.8,
        "case_sensitive": False,
    }

    def __init__(
        self,
        memory_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}

        self.memory_dir = Path(memory_dir) if memory_dir else PROJECT_ROOT / "memory"
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR

        self._lock = threading.Lock()

        self._sources = {
            "chat_history": self._search_chat_history,
            "skills": self._search_skills,
        }

        logger.info(
            f"[GlobalSearch] 初始化完成 - "
            f"搜索源: {list(self._sources.keys())}, "
            f"最大结果数: {self.config['max_results_per_source']}"
        )

    def register_source(self, name: str, search_func: callable):
        """注册新的搜索源"""
        self._sources[name] = search_func
        logger.info(f"[GlobalSearch] 注册搜索源: {name}")

    def search(
        self,
        query: Union[str, List[str]],
        sources: Optional[List[str]] = None,
        use_regex: bool = True,
        case_sensitive: bool = False,
        max_results: Optional[int] = None,
    ) -> List[SearchResult]:
        """
        全局搜索

        Args:
            query: 搜索关键词或正则表达式（可传入列表表示多个关键词）
            sources: 指定搜索的数据源，None 表示全部
            use_regex: 是否使用正则表达式匹配
            case_sensitive: 是否区分大小写
            max_results: 最大结果数，None 表示使用配置值

        Returns:
            搜索结果列表
        """
        if max_results is None:
            max_results = self.config["max_results_per_source"]

        if isinstance(query, str):
            keywords = [query]
        else:
            keywords = query

        target_sources = sources if sources else list(self._sources.keys())

        all_results: List[SearchResult] = []

        for source_name in target_sources:
            if source_name not in self._sources:
                logger.warning(f"[GlobalSearch] 未知数据源: {source_name}")
                continue

            try:
                results = self._sources[source_name](
                    keywords=keywords,
                    use_regex=use_regex,
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                )
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[GlobalSearch] 搜索 {source_name} 失败: {e}", exc_info=True)

        all_results.sort(key=lambda x: x.relevance_score, reverse=True)

        return all_results[:max_results]

    def search_chat_history(
        self,
        keywords: List[str],
        use_regex: bool = True,
        case_sensitive: bool = False,
        max_results: int = 10,
    ) -> List[SearchResult]:
        """专门搜索对话历史"""
        return self._search_chat_history(keywords, use_regex, case_sensitive, max_results)

    def _search_chat_history(
        self,
        keywords: List[str],
        use_regex: bool,
        case_sensitive: bool,
        max_results: int,
    ) -> List[SearchResult]:
        """搜索完整对话历史"""
        results: List[SearchResult] = []

        history_file = self.memory_dir / "chat_history.json"
        if not history_file.exists():
            logger.debug(f"[GlobalSearch] 对话历史文件不存在: {history_file}")
            return results

        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            full_messages = data.get("full_messages", [])

            for msg_idx, msg in enumerate(full_messages):
                msg_text = msg.get("content", "")
                if not msg_text:
                    continue

                match_info = self._check_match(
                    msg_text, keywords, use_regex, case_sensitive
                )

                if match_info["matched"]:
                    is_human = msg.get("type") == "human"
                    msg_role = "用户" if is_human else "助手"

                    result = SearchResult(
                        source=f"对话历史 (第{msg_idx + 1}条)",
                        source_type="chat_history",
                        title=f"{msg_role}消息",
                        matched_content=match_info["matched_text"],
                        context_before=match_info["context_before"],
                        context_after=match_info["context_after"],
                        relevance_score=match_info["score"],
                        line_number=msg_idx + 1,
                    )
                    results.append(result)

                    if len(results) >= max_results:
                        break

        except Exception as e:
            logger.error(f"[GlobalSearch] 搜索对话历史失败: {e}", exc_info=True)

        logger.debug(f"[GlobalSearch] 对话历史搜索完成: {len(results)} 条结果")
        return results

    def search_skills(
        self,
        keywords: List[str],
        use_regex: bool = True,
        case_sensitive: bool = False,
        max_results: int = 10,
        skill_name: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        搜索 Skills 文档

        Args:
            skill_name: 如果指定，只搜索该 skill
        """
        return self._search_skills(keywords, use_regex, case_sensitive, max_results, skill_name)

    def _search_skills(
        self,
        keywords: List[str],
        use_regex: bool,
        case_sensitive: bool,
        max_results: int,
        skill_name: Optional[str] = None,
    ) -> List[SearchResult]:
        """搜索 Skills 文档"""
        results: List[SearchResult] = []

        if not self.skills_dir.exists():
            logger.debug(f"[GlobalSearch] Skills目录不存在: {self.skills_dir}")
            return results

        try:
            if skill_name:
                skill_paths = [self.skills_dir / skill_name / "SKILL.md"]
            else:
                skill_paths = list(self.skills_dir.glob("*/SKILL.md"))

            for skill_path in skill_paths:
                skill_dir = skill_path.parent
                skill_nm = skill_dir.name

                try:
                    content = skill_path.read_text(encoding="utf-8")
                    lines = content.split("\n")

                    for line_idx, line in enumerate(lines):
                        if not line.strip():
                            continue

                        match_info = self._check_match(
                            line, keywords, use_regex, case_sensitive
                        )

                        if match_info["matched"]:
                            title = self._extract_title(lines, line_idx, skill_nm)

                            result = SearchResult(
                                source=f"Skill: {skill_nm}",
                                source_type="skill",
                                title=title,
                                matched_content=match_info["matched_text"],
                                context_before=match_info["context_before"],
                                context_after=match_info["context_after"],
                                relevance_score=match_info["score"],
                                line_number=line_idx + 1,
                            )
                            results.append(result)

                except Exception as e:
                    logger.error(f"[GlobalSearch] 搜索 {skill_nm} 失败: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[GlobalSearch] 搜索Skills失败: {e}", exc_info=True)

        logger.debug(f"[GlobalSearch] Skills搜索完成: {len(results)} 条结果")
        return results

    def _check_match(
        self,
        text: str,
        keywords: List[str],
        use_regex: bool,
        case_sensitive: bool,
    ) -> Dict[str, Any]:
        """检查文本是否匹配"""
        result = {
            "matched": False,
            "matched_text": "",
            "context_before": "",
            "context_after": "",
            "score": 0.0,
        }

        max_context = self.config["max_context_chars"]

        if use_regex:
            for keyword in keywords:
                try:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    pattern = re.compile(keyword, flags)

                    for match in pattern.finditer(text):
                        result["matched"] = True
                        start, end = match.start(), match.end()
                        result["matched_text"] = match.group()

                        result["context_before"] = text[max(0, start - max_context):start]
                        result["context_after"] = text[end:min(len(text), end + max_context)]

                        result["score"] = self._calculate_score(
                            text, match.group(), len(keywords)
                        )
                        return result

                except re.error as e:
                    logger.warning(f"[GlobalSearch] 正则表达式错误: {keyword} - {e}")
                    continue
        else:
            text_to_search = text if case_sensitive else text.lower()
            for keyword in keywords:
                search_term = keyword if case_sensitive else keyword.lower()

                if search_term in text_to_search:
                    start = text_to_search.index(search_term)
                    end = start + len(keyword)

                    result["matched"] = True
                    result["matched_text"] = text[start:end]
                    result["context_before"] = text[max(0, start - max_context):start]
                    result["context_after"] = text[end:min(len(text), end + max_context)]

                    result["score"] = self._calculate_score(
                        text, keyword, len(keywords)
                    )
                    return result

        return result

    def _calculate_score(self, text: str, matched_text: str, keyword_count: int) -> float:
        """计算相关性分数"""
        base_score = len(matched_text) / max(len(text), 1)
        keyword_bonus = 0.1 * keyword_count
        return min(1.0, base_score + keyword_bonus)

    def _extract_title(self, lines: List[str], current_idx: int, default: str) -> str:
        """提取标题"""
        for i in range(current_idx, max(0, current_idx - 5), -1):
            line = lines[i].strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()
            if line.startswith("**") and "**" in line[2:]:
                return line.strip("*")
        return default

    def format_results(
        self,
        results: List[SearchResult],
        format_type: str = "markdown",
        max_results: Optional[int] = None,
    ) -> str:
        """
        格式化搜索结果

        Args:
            results: 搜索结果列表
            format_type: 输出格式 ("markdown", "json", "simple")
            max_results: 最大结果数

        Returns:
            格式化后的字符串
        """
        if not results:
            return "未找到匹配结果"

        if max_results:
            results = results[:max_results]

        if format_type == "json":
            return json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)

        if format_type == "simple":
            lines = [f"找到 {len(results)} 条结果："]
            for i, r in enumerate(results, 1):
                lines.append(f"\n{i}. [{r.source_type}] {r.title}")
                lines.append(f"   {r.matched_content}")
            return "\n".join(lines)

        lines = [f"## 全局搜索结果（共 {len(results)} 条）\n"]

        grouped: Dict[str, List[SearchResult]] = {}
        for r in results:
            if r.source_type not in grouped:
                grouped[r.source_type] = []
            grouped[r.source_type].append(r)

        for source_type, items in grouped.items():
            lines.append(f"\n### 📁 {source_type.upper()}\n")
            for item in items:
                lines.append(item.to_markdown())

        return "\n".join(lines)

    def search_multiple_sources(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        max_per_source: int = 5,
    ) -> Dict[str, List[SearchResult]]:
        """并行搜索多个数据源"""
        if sources is None:
            sources = list(self._sources.keys())

        results: Dict[str, List[SearchResult]] = {}

        for source in sources:
            results[source] = self.search(
                query=query,
                sources=[source],
                max_results=max_per_source,
            )

        return results


class DualMemorySearch:
    """
    集成到 DualMemory 的搜索功能

    提供便捷的搜索接口，优先搜索对话历史
    """

    def __init__(self, dual_memory: Any):
        self.memory = dual_memory
        self.memory_dir = dual_memory.memory_dir
        self._searcher = GlobalSearch(memory_dir=self.memory_dir)

    def search_conversation(
        self,
        keywords: Union[str, List[str]],
        max_results: int = 5,
    ) -> str:
        """
        搜索对话历史

        Args:
            keywords: 搜索关键词
            max_results: 最大结果数

        Returns:
            格式化后的搜索结果
        """
        results = self._searcher.search_chat_history(
            keywords=[keywords] if isinstance(keywords, str) else keywords,
            max_results=max_results,
        )
        return self._searcher.format_results(results)

    def search_all(
        self,
        keywords: Union[str, List[str]],
        include_skills: bool = True,
        max_results: int = 10,
    ) -> str:
        """
        全局搜索

        Args:
            keywords: 搜索关键词
            include_skills: 是否包含 Skills 文档
            max_results: 最大结果数

        Returns:
            格式化后的搜索结果
        """
        sources = ["chat_history"]
        if include_skills:
            sources.append("skills")

        results = self._searcher.search(
            query=keywords,
            sources=sources,
            max_results=max_results,
        )
        return self._searcher.format_results(results)

    def quick_search(self, keyword: str) -> List[str]:
        """
        快速搜索，返回匹配文本列表

        用于模型快速检查是否包含某个关键词
        """
        results = self._searcher.search_chat_history(
            keywords=[keyword],
            use_regex=False,
            max_results=20,
        )
        return [r.matched_content for r in results]
