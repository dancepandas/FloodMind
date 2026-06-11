"""
Background Review — 后台回顾系统

每轮 SSE 流结束后，在后台线程中用轻量 LLM 回顾本轮对话：
  1. 提取用户偏好 → 写入长期记忆
  2. 提取任务经验 → 写入经验树
  3. 生成 skill 改进建议 → 写入待审核队列

设计原则：
  - 轻量：不 fork 完整 Agent，单次 LLM 调用
  - 安全：只操作 memory/experience，不直接修改 skill 文件
  - 可控：通过开关和配置项管理行为
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from floodmind.agent.native.model_client import ModelClient

logger = logging.getLogger(__name__)

# ── Review Prompts ────────────────────────────────────────────

_REVIEW_PROMPT = """你是一个对话回顾助手。请分析以下对话记录，提取有价值的信息。

## 对话记录
{conversation_text}

## 提取要求
请输出一个 JSON 对象，包含以下字段（没有相关内容则返回空列表或空字符串）：

1. `user_preferences`: 用户偏好列表
   - 每个元素包含 `content`(内容) 和 `type`(类型: preference/decision/rule)
   - 例如用户说"以后不要用这个模型"、"输出要包含置信区间"
   - 只提取明确的、可复用的偏好，不要推测

2. `experience`: 任务执行经验（如果有工具调用）
   - `tree_path`: 经验树路径列表，如 ["水文预报", "模型运行"]
   - `task_description`: 任务描述（泛化，去除具体路径）
   - `domain_keywords`: 关键词列表
   - `skill_used`: 使用的 skill 名称
   - `pitfalls`: 坑点列表
   - `solutions`: 解决方案列表
   - `importance`: 重要性 0.0-1.0
   - 如果对话没有工具调用或任务执行，此项为空对象

3. `skill_suggestions`: Skill 改进建议列表
   - 每个元素包含 `skill_name`(skill 名)、`suggestion`(建议内容)、`reason`(原因)
   - 只针对明确使用过或提及的 skill 提出建议
   - 不直接修改 skill，只记录建议供后续审核

## 规则
- 泛化：去除具体文件路径、session_id、时间戳
- 保守：不确定的信息不要提取
- 只提取本轮对话中出现的新信息，不要重复已有常识
- 如果对话只是简单问答，没有工具调用，输出空 JSON

## 输出格式
严格输出 JSON，不要添加任何其他文字：
```json
{"user_preferences": [], "experience": null, "skill_suggestions": []}
```
"""


@dataclass
class ReviewResult:
    """回顾结果"""

    user_preferences: List[Dict[str, str]]
    experience: Optional[Dict[str, Any]]
    skill_suggestions: List[Dict[str, str]]
    raw_json: str = ""


class BackgroundReviewer:
    """
    后台回顾器。

    使用方式：
        reviewer = BackgroundReviewer(model_client)
        reviewer.review_session(session_id, messages)
    """

    def __init__(
        self,
        model_client: ModelClient,
        enabled: bool = True,
        min_message_count: int = 3,
    ):
        self.model_client = model_client
        self.enabled = enabled
        self.min_message_count = min_message_count

    def review_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> Optional[ReviewResult]:
        """
        回顾单轮会话，返回结构化结果。
        不直接写入任何存储，由调用方决定如何应用。
        """
        if not self.enabled:
            return None
        if len(messages) < self.min_message_count:
            return None

        conversation_text = self._format_messages(messages)
        if len(conversation_text) < 200:
            return None

        prompt = _REVIEW_PROMPT.format(conversation_text=conversation_text)

        try:
            response = self.model_client.invoke(
                prompt=prompt,
                system_prompt="你是一个对话回顾助手，擅长从对话中提取结构化经验。",
                temperature=0.2,
                max_tokens=1500,
            )
            raw = response.content if hasattr(response, "content") else str(response)
            result = self._parse_result(raw)
            if result:
                logger.info(
                    "[BackgroundReview] session=%s preferences=%d experience=%s suggestions=%d",
                    session_id,
                    len(result.user_preferences),
                    "yes" if result.experience else "no",
                    len(result.skill_suggestions),
                )
            return result
        except Exception as e:
            logger.warning("[BackgroundReview] review failed: %s", e)
            return None

    def apply_review_result(
        self,
        session_id: str,
        result: ReviewResult,
        memory_instance: Any = None,
        experience_tree: Any = None,
    ) -> Dict[str, Any]:
        """
        应用回顾结果到存储。
        返回应用统计。
        """
        applied = {"preferences": 0, "experiences": 0, "suggestions": 0}

        # 1. 写入长期记忆
        for pref in result.user_preferences:
            content = pref.get("content", "").strip()
            entry_type = pref.get("type", "preference")
            if not content:
                continue
            try:
                if memory_instance and hasattr(memory_instance, "add_long_term_memory"):
                    memory_instance.add_long_term_memory(content, entry_type)
                    applied["preferences"] += 1
            except Exception as e:
                logger.debug("[BackgroundReview] memory write failed: %s", e)

        # 2. 写入经验树
        if result.experience and experience_tree:
            try:
                from floodmind.memory.experience_tree import ExperienceLeaf

                exp = result.experience
                tree_path = exp.get("tree_path", ["通用经验"])
                if not tree_path:
                    tree_path = ["通用经验"]

                leaf = ExperienceLeaf(
                    node_id="",
                    path=tree_path,
                    label=tree_path[-1] if tree_path else "未分类",
                    task_description=exp.get("task_description", ""),
                    domain_keywords=exp.get("domain_keywords", []),
                    skill_used=exp.get("skill_used", ""),
                    steps_summary="",
                    pitfalls=exp.get("pitfalls", []),
                    solutions=exp.get("solutions", []),
                    code_snippets=[],
                    final_outcome="success",
                    session_id=session_id,
                    importance=exp.get("importance", 0.5),
                )
                experience_tree.add_leaf(leaf, tree_path[:-1] if len(tree_path) > 1 else ["通用经验"])
                applied["experiences"] += 1
            except Exception as e:
                logger.debug("[BackgroundReview] experience write failed: %s", e)

        # 3. 记录 skill 建议到待审核队列
        if result.skill_suggestions:
            try:
                self._queue_skill_suggestions(session_id, result.skill_suggestions)
                applied["suggestions"] = len(result.skill_suggestions)
            except Exception as e:
                logger.debug("[BackgroundReview] suggestion queue failed: %s", e)

        logger.info(
            "[BackgroundReview] applied: preferences=%d experiences=%d suggestions=%d",
            applied["preferences"],
            applied["experiences"],
            applied["suggestions"],
        )
        return applied

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        """将消息列表格式化为回顾用的文本"""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            if role == "user":
                parts.append(f"[用户] {content[:800]}")
            elif role == "assistant":
                parts.append(f"[助手] {content[:800]}")
            elif role in ("tool", "function"):
                parts.append(f"[工具结果] {content[:600]}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_result(raw: str) -> Optional[ReviewResult]:
        """解析 LLM 返回的 JSON"""
        text = raw.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        if not text or text in ("{}", "null"):
            return None

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return None

            prefs = data.get("user_preferences") or []
            exp = data.get("experience")
            if exp and not isinstance(exp, dict):
                exp = None
            suggestions = data.get("skill_suggestions") or []

            return ReviewResult(
                user_preferences=prefs if isinstance(prefs, list) else [],
                experience=exp,
                skill_suggestions=suggestions if isinstance(suggestions, list) else [],
                raw_json=text,
            )
        except json.JSONDecodeError:
            logger.debug("[BackgroundReview] JSON parse failed: %s...", text[:200])
            return None

    @staticmethod
    def _queue_skill_suggestions(session_id: str, suggestions: List[Dict[str, str]]) -> None:
        """将 skill 建议写入待审核队列文件"""
        import os
        from pathlib import Path
        from datetime import datetime

        queue_dir = Path(".floodmind") / "skill_suggestions"
        queue_dir.mkdir(parents=True, exist_ok=True)

        queue_file = queue_dir / f"{session_id}.json"
        entries = []
        if queue_file.exists():
            try:
                entries = json.loads(queue_file.read_text("utf-8"))
            except Exception:
                entries = []

        for s in suggestions:
            entries.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "session_id": session_id,
                    "skill_name": s.get("skill_name", ""),
                    "suggestion": s.get("suggestion", ""),
                    "reason": s.get("reason", ""),
                    "status": "pending",
                }
            )

        queue_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def spawn_background_review(
    session_id: str,
    messages: List[Dict[str, Any]],
    model_client: ModelClient,
    memory_instance: Any = None,
    experience_tree: Any = None,
) -> None:
    """
    在后台线程中执行会话回顾。

    使用方式（在 SSE 流结束后调用）：
        threading.Thread(
            target=spawn_background_review,
            args=(session_id, messages, model_client, memory, exp_tree),
            daemon=True,
        ).start()
    """

    def _worker():
        reviewer = BackgroundReviewer(model_client)
        result = reviewer.review_session(session_id, messages)
        if result:
            reviewer.apply_review_result(session_id, result, memory_instance, experience_tree)

    threading.Thread(target=_worker, daemon=True, name=f"bg-review-{session_id[:8]}").start()
