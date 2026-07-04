"""
任务执行经验核心模块

经验以树状层级组织（ExperienceTree），每次变更同步持久化到 JSON。
Agent 启动任务时读取摘要注入上下文，也可通过工具检索。

支持渐进压缩：叶子超过阈值时 LLM 生成摘要节点（SummaryNode），
上下文注入只给摘要而非全部叶子。
支持热度衰减：高频经验提升搜索排名，低频归档。
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from floodmind.config.settings import settings
from floodmind.memory.experience_tree import ExperienceLeaf, ExperienceTree, SummaryNode, SUMMARY_PROMPT

logger = logging.getLogger(__name__)

# Skill 生成后回调：由 NativeFloodAgent.refresh_skills 注册
_on_skill_generated = None


# ── 经验提取 Prompt ──────────────────────────────────────────

EXTRACTION_PROMPT = """你是一个经验提取专家。请分析以下任务执行记录，提取结构化的任务执行经验。

## 任务执行记录
用户输入: {user_input}
执行计划: {plan_summary}
工具调用记录: {tool_calls_summary}
最终输出: {final_output_summary}
是否遇到错误: {had_errors}
执行耗时: {duration_seconds}秒

## 提取要求
请输出一个 JSON 对象，包含以下字段：

1. `tree_path`: 建议的树路径（从领域到具体案例的层级列表），例如 ["水文预报", "敖江流域预报", "霍口水库断面预报"]
   - 第一层是大的领域分类（如：水文预报、数据导出、绘图、报告生成、其他）
   - 中间层是子领域或任务类型（如：敖江流域预报、Excel导出）
   - 最后一层是具体案例名称（如：霍口水库断面预报）
   - 路径应泛化，不要包含具体的session_id或时间戳

2. `task_description`: 任务的简短描述（1-2句话，泛化描述，去除具体路径和session信息）

3. `domain_keywords`: 相关领域关键词列表（如 ["aojiang", "huokou", "forecast", "excel"]）

4. `skill_used`: 使用的 skill 名称（如 "chronos"，没有则为空字符串）

5. `steps_summary`: 关键步骤摘要（按顺序列出主要步骤，每步一句话）

6. `pitfalls`: 遇到的坑点/错误列表（每个坑点独立描述，泛化后保留模式；如果没有坑点则为空列表）

7. `solutions`: 对应的解决方案/变通方法列表（与坑点一一对应或独立描述；如果没有则为空列表）

8. `code_snippets`: 任务中编写的关键脚本/代码片段列表（可复用的代码，去除硬编码路径，保留核心逻辑；如果没有则为空列表）

9. `final_outcome`: 最终结果 ("success" | "partial" | "failed")

10. `importance`: 重要性评分 (0.0-1.0)
   - 有坑点且成功解决: 0.8-1.0
   - 有坑点但未完全解决: 0.6-0.8
   - 顺利完成的常规任务: 0.3-0.5
   - 简单问答类: 0.1-0.2

## 重要规则
- 泛化经验：去除具体文件路径、session_id、时间戳，保留可复用的模式和规律
- 坑点要具体描述问题本质，不要只写"出错了"
- 解决方案要可操作，不要只写"修复了"
- 如果任务太简单（如问候、简单查询），返回空 JSON {{}}

## 输出格式
严格输出 JSON，不要添加任何其他文字：
```json
{{...}}
```"""


MERGE_PROMPT = """请将以下多条重复/相似的任务执行经验合并为一条更完整的经验。

## 待合并经验
{experiences}

## 合并要求
1. 保留所有独特的坑点和解决方案（去重后合并）
2. 取最完整的步骤摘要
3. 保留所有可复用代码片段
4. importance 取最高值
5. final_outcome 取最成功的结果（success > partial > failed）
6. 泛化描述，去除具体路径和 session 信息

## 输出格式
严格输出 JSON，不要添加任何其他文字：
```json
{{"task_description": "...", "pitfalls": [...], "solutions": [...], "steps_summary": "...", "code_snippets": [...], "final_outcome": "success", "importance": 0.8}}
```"""


# ── TaskExperienceStore ──────────────────────────────────────

class TaskExperienceStore:
    """经验存储管理器 — 基于树索引 JSON，支持渐进压缩和热度衰减"""

    _instance: Optional['TaskExperienceStore'] = None
    _lock = threading.Lock()

    def __new__(cls, persist_dir: str = ""):
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cls._instance

    def __init__(self, persist_dir: str = ""):
        with self._lock:
            if self._initialized:
                return

            cfg = settings.task_experience
            self.persist_dir = persist_dir or cfg.persist_dir
            self._tree: Optional[ExperienceTree] = None
            self._llm_service = None
            self._version: int = 0
            self._initialized = True

    def set_llm_service(self, llm_service) -> None:
        """注入 LLM 服务（用于摘要生成）"""
        self._llm_service = llm_service

    @property
    def tree(self) -> ExperienceTree:
        if self._tree is None:
            self._tree = ExperienceTree(persist_dir=self.persist_dir)
        return self._tree

    def record_experience(self, leaf: ExperienceLeaf, tree_path: List[str]) -> ExperienceLeaf:
        """存储经验到树索引 JSON，并在积累足够经验后触发 seal"""
        added_leaf = self.tree.add_leaf(leaf, tree_path)
        self._version += 1
        logger.info(f"经验已记录: path={added_leaf.path}, outcome={added_leaf.final_outcome}, version={self._version}")
        try:
            self.seal_if_needed()
        except Exception as e:
            logger.error(f"seal_if_needed 失败: {e}")
        return added_leaf

    def update_experience(self, node_id: str, **kwargs) -> bool:
        """增量更新指定经验叶子"""
        ok = self.tree.update_leaf(node_id, **kwargs)
        if ok:
            self._version += 1
        return ok

    def mark_helpful(self, node_id: str) -> bool:
        """标记经验有帮助：success_count++ 并重新计算 importance"""
        with self.tree._lock:
            leaf = self.tree._leaves.get(node_id)
            if not leaf:
                return False
            leaf.success_count += 1
            leaf.base_importance = min(1.0, leaf.base_importance + 0.05)
            leaf.recompute_importance()
            self.tree._save()
            self._version += 1
            logger.info(f"经验标记为有帮助: node_id={node_id}, success={leaf.success_count}, importance={leaf.importance:.2f}")
            return True

    def mark_not_helpful(self, node_id: str) -> bool:
        """标记经验无帮助：failure_count++ 并重新计算 importance"""
        with self.tree._lock:
            leaf = self.tree._leaves.get(node_id)
            if not leaf:
                return False
            leaf.failure_count += 1
            leaf.base_importance = max(0.1, leaf.base_importance - 0.05)
            leaf.recompute_importance()
            self.tree._save()
            self._version += 1
            logger.info(f"经验标记为无帮助: node_id={node_id}, failure={leaf.failure_count}, importance={leaf.importance:.2f}")
            return True

    def has_experiences(self) -> bool:
        return self.tree.get_stats()["leaf_cases"] > 0

    def get_version(self) -> int:
        return self._version

    # ── 渐进压缩 ──────────────────────────────────────────────

    def seal_if_needed(self) -> List[SummaryNode]:
        """遍历所有分支，叶子数 >= threshold 的触发 seal"""
        cfg = settings.task_experience
        threshold = getattr(cfg, "seal_threshold", 5)
        branches = self.tree.get_branches_needing_seal(threshold)

        sealed = []
        for path, leaf_count in branches:
            try:
                summary_text = self._generate_summary(path)
                if summary_text:
                    node = self.tree.seal_branch(path, summary_text)
                    sealed.append(node)
                    logger.info(f"seal 完成: path={path}, {leaf_count} 条经验")

                    # 经验→Skill 自动生成
                    threshold = getattr(cfg, "skill_generation_threshold", 5)
                    if leaf_count >= threshold:
                        try:
                            self._try_generate_skill(path, leaf_count, summary_text)
                        except Exception as e:
                            logger.warning(f"Skill 生成失败 path={path}: {e}")
            except Exception as e:
                logger.error(f"seal 失败 path={path}: {e}")

        return sealed

    def _try_generate_skill(self, path: List[str], leaf_count: int, summary_text: str) -> None:
        """尝试从密封分支生成 Skill"""
        from floodmind.memory.skill_generator import generate_skill_from_branch, write_skill_to_disk

        node = self.tree.find_node(path)
        if not node:
            return
        leaves = self.tree.get_leaves(node.node_id)
        if not leaves:
            return

        skill_slug = "-".join(p.replace(" ", "-") for p in path)
        skill_content = generate_skill_from_branch(
            leaves=leaves,
            summary_text=summary_text,
            skill_slug=skill_slug,
            llm_service=self._llm_service,
        )
        if skill_content:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            skill_dir = os.path.join(project_root, "skills", skill_slug)
            filepath = write_skill_to_disk(skill_content, skill_dir)
            if filepath and _on_skill_generated:
                try:
                    _on_skill_generated()
                except Exception as e:
                    logger.warning(f"Skill 生成后回调失败: {e}")

    def _generate_summary(self, path: List[str]) -> Optional[str]:
        """调用 LLM 为指定路径生成摘要"""
        if not self._llm_service:
            return self._generate_summary_local(path)

        try:
            node = self.tree.find_node(path)
            if not node:
                return None
            leaves = self.tree.get_leaves(node.node_id)
            if not leaves:
                return None

            experiences = "\n\n".join(
                f"经验{i+1}:\n{leaf.to_searchable_text()}"
                for i, leaf in enumerate(leaves)
            )
            prompt = SUMMARY_PROMPT.format(experiences=experiences)

            response = self._llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            raw = response.content if hasattr(response, 'content') else str(response)
            return raw.strip()
        except Exception as e:
            logger.error(f"LLM 摘要生成失败: {e}")
            return self._generate_summary_local(path)

    def _generate_summary_local(self, path: List[str]) -> Optional[str]:
        """本地 fallback 摘要：统计式摘要，不依赖 LLM"""
        node = self.tree.find_node(path)
        if not node:
            return None
        leaves = self.tree.get_leaves(node.node_id)
        if not leaves:
            return None

        success_count = sum(1 for l in leaves if l.final_outcome == "success")
        all_pitfalls = []
        all_solutions = []
        for leaf in leaves:
            all_pitfalls.extend(leaf.pitfalls)
            all_solutions.extend(leaf.solutions)

        parts = [f"共{len(leaves)}条经验，{success_count}/{len(leaves)}成功。"]
        if all_pitfalls:
            unique_pitfalls = list(dict.fromkeys(all_pitfalls))[:5]
            parts.append(f"常见坑点: {'; '.join(unique_pitfalls)}")
        if all_solutions:
            unique_solutions = list(dict.fromkeys(all_solutions))[:5]
            parts.append(f"核心方案: {'; '.join(unique_solutions)}")
        return "".join(parts)

    # ── 热度管理 ──────────────────────────────────────────────

    def bump_hotness(self, query: str, matched_leaves: List[ExperienceLeaf]) -> None:
        """搜索命中后更新热度"""
        for leaf in matched_leaves:
            self.tree.bump_hotness(leaf.node_id)

    def _hotness_score(self, leaf: ExperienceLeaf) -> float:
        """计算热度加权分"""
        score = leaf.hit_count * 0.1
        if leaf.last_hit_at:
            try:
                last_hit = datetime.fromisoformat(leaf.last_hit_at)
                days_ago = (datetime.now() - last_hit).days
                decay_days = getattr(settings.task_experience, "hotness_decay_days", 90)
                if days_ago <= 7:
                    score += 0.5
                elif days_ago <= 30:
                    score += 0.3
                elif days_ago > decay_days:
                    score -= 0.1
            except (ValueError, TypeError):
                pass
        return score

    # ── 上下文注入 ────────────────────────────────────────────

    def build_summary_context(self) -> str:
        """构建摘要层上下文（≤1500 token），替代整棵树注入"""
        return self.tree.render_summary_markdown()

    # ── 浏览与下钻 ────────────────────────────────────────────

    def browse_tree(self, path: str = "") -> str:
        """按路径浏览树结构（只返回结构 + 摘要，不返回叶子详情）"""
        if path:
            return self.tree.render_path_markdown(path)
        return self.tree.render_summary_markdown()

    def drill_down(self, summary_node_id: str) -> str:
        """从摘要展开到叶子详情"""
        leaves = self.tree.drill_down(summary_node_id)
        if not leaves:
            return f"未找到摘要节点: {summary_node_id}"
        return self.render_experience_markdown(leaves)

    # ── 关键词检索 ────────────────────────────────────────────

    def search_keywords(self, query: str, path_filter: str = "", top_k: int = 5) -> List[ExperienceLeaf]:
        """关键词检索经验（基于树索引，热度加权）"""
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 1]

        all_leaves = self.tree.get_all_leaves()

        if path_filter:
            filter_parts = [p.strip() for p in path_filter.split("/") if p.strip()]
            all_leaves = [
                leaf for leaf in all_leaves
                if any(fp.lower() in "/".join(leaf.path).lower() for fp in filter_parts)
            ]

        candidates: List[tuple] = []
        for leaf in all_leaves:
            score = 0.0
            path_lower = "/".join(leaf.path).lower()
            pitfalls_lower = " ".join(leaf.pitfalls).lower()
            solutions_lower = " ".join(leaf.solutions).lower()
            searchable_parts = [
                path_lower,
                leaf.task_description.lower(),
                leaf.skill_used.lower(),
                " ".join(leaf.domain_keywords).lower(),
                pitfalls_lower,
                solutions_lower,
                leaf.steps_summary.lower(),
            ]
            searchable_text = " ".join(searchable_parts)

            for word in query_words:
                if word in searchable_text:
                    score += 1.0
                    if word in path_lower:
                        score += 0.5
                    if word in pitfalls_lower:
                        score += 0.3
                    if word in solutions_lower:
                        score += 0.3

            score += leaf.importance * 0.2
            score += self._hotness_score(leaf)

            if score > 0:
                candidates.append((score, leaf))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [leaf for _, leaf in candidates[:top_k]]

    def render_experience_markdown(self, leaves: List[ExperienceLeaf]) -> str:
        """将叶子节点列表渲染为可读 Markdown"""
        if not leaves:
            return "未找到相关任务执行经验。"

        parts = []
        for i, leaf in enumerate(leaves, 1):
            path_str = "/".join(leaf.path)
            parts.append(f"### 经验 {i}: {path_str}")
            parts.append(f"- **描述**: {leaf.task_description}")
            parts.append(f"- **结果**: {leaf.final_outcome}")
            if leaf.skill_used:
                parts.append(f"- **使用 Skill**: {leaf.skill_used}")
            if leaf.steps_summary:
                parts.append(f"- **步骤摘要**: {leaf.steps_summary}")
            if leaf.pitfalls:
                parts.append(f"- **坑点**: {'; '.join(leaf.pitfalls)}")
            if leaf.solutions:
                parts.append(f"- **解决方案**: {'; '.join(leaf.solutions)}")
            if leaf.code_snippets:
                parts.append(f"- **可复用代码**: {'; '.join(leaf.code_snippets)}")
            parts.append("")

        return "\n".join(parts)

    # ── 定期巡检 ──────────────────────────────────────────────

    def should_run_maintenance(self) -> bool:
        """检查是否应该执行巡检（基于时间标记文件）"""
        cfg = settings.task_experience
        marker_file = os.path.join(cfg.persist_dir, ".last_maintenance")
        if not os.path.exists(marker_file):
            return True
        try:
            content = Path(marker_file).read_text(encoding="utf-8").strip()
            last_time = datetime.fromisoformat(content)
            interval = timedelta(hours=cfg.maintenance_interval_hours)
            return datetime.now() - last_time >= interval
        except Exception:
            return True

    def _mark_maintenance_done(self) -> None:
        """写入巡检完成时间标记"""
        cfg = settings.task_experience
        marker_file = os.path.join(cfg.persist_dir, ".last_maintenance")
        try:
            with open(marker_file, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat())
        except Exception as e:
            logger.warning(f"写入巡检标记失败: {e}")

    def run_maintenance(self, llm_service=None) -> Dict[str, int]:
        """执行经验树巡检：去重 → 归档 → seal

        返回巡检统计：{"merged": N, "archived": N, "removed": N, "sealed": N}
        """
        cfg = settings.task_experience
        stats = {"merged": 0, "archived": 0, "removed": 0, "sealed": 0}

        # Step 1: 去重合并
        try:
            groups = self.tree.find_duplicate_groups(cfg.dedup_similarity_threshold)
            logger.info(f"巡检: 发现 {len(groups)} 组重复经验")
            for group in groups:
                try:
                    merged_leaf = self._merge_group(group, llm_service)
                    if merged_leaf:
                        parent_path = merged_leaf.path[:-1]
                        old_ids = [leaf.node_id for leaf in group]
                        self.tree.merge_leaves(old_ids, merged_leaf, parent_path)
                        stats["merged"] += 1
                        logger.info(f"巡检: 合并 {len(group)} 条经验 → {merged_leaf.path}")
                except Exception as e:
                    logger.warning(f"巡检: 合并经验组失败: {e}")
        except Exception as e:
            logger.error(f"巡检: 去重步骤失败: {e}")

        # Step 2: 归档过时经验
        try:
            now = datetime.now()
            archive_threshold = timedelta(days=cfg.archive_after_days)
            all_leaves = self.tree.get_all_leaves()
            for leaf in all_leaves:
                # hit_count == 0 且创建时间超过归档天数 → 归档
                if leaf.hit_count == 0 and leaf.created_at:
                    try:
                        created = datetime.fromisoformat(leaf.created_at)
                        if now - created >= archive_threshold:
                            self.tree.archive_leaf(leaf.node_id)
                            stats["archived"] += 1
                    except (ValueError, TypeError):
                        pass
                # importance < 0.2 且无坑点无方案 → 删除
                if leaf.importance < 0.2 and not leaf.pitfalls and not leaf.solutions:
                    self.tree.remove_leaf(leaf.node_id)
                    stats["removed"] += 1
        except Exception as e:
            logger.error(f"巡检: 归档步骤失败: {e}")

        # Step 3: seal 检查
        try:
            sealed = self.seal_if_needed()
            stats["sealed"] = len(sealed)
        except Exception as e:
            logger.error(f"巡检: seal 步骤失败: {e}")

        self._mark_maintenance_done()
        logger.info(f"巡检完成: 合并={stats['merged']}, 归档={stats['archived']}, 删除={stats['removed']}, seal={stats['sealed']}")
        return stats

    def _merge_group(self, group: List[ExperienceLeaf], llm_service=None) -> Optional[ExperienceLeaf]:
        """LLM 合并一组重复经验，返回合并后的 ExperienceLeaf"""
        service = llm_service or self._llm_service
        if not service:
            # 无 LLM 时取最完整的叶子作为合并结果
            best = max(group, key=lambda l: (len(l.pitfalls) + len(l.solutions), l.importance))
            merged_pitfalls = list(dict.fromkeys(p for l in group for p in l.pitfalls))
            merged_solutions = list(dict.fromkeys(s for l in group for s in l.solutions))
            merged_code = list(dict.fromkeys(c for l in group for c in l.code_snippets))
            best.pitfalls = merged_pitfalls
            best.solutions = merged_solutions
            best.code_snippets = merged_code
            best.importance = max(l.importance for l in group)
            return best

        experiences = "\n\n".join(
            f"经验{i+1}:\n{leaf.to_searchable_text()}"
            for i, leaf in enumerate(group)
        )
        prompt = MERGE_PROMPT.format(experiences=experiences)

        try:
            response = service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            raw = response.content if hasattr(response, 'content') else str(response)
            text = raw.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)
            if not data:
                return None

            # 取第一条的路径和基本信息作为基础
            base = group[0]
            steps_summary_raw = data.get("steps_summary", "")
            steps_summary = "; ".join(steps_summary_raw) if isinstance(steps_summary_raw, list) else str(steps_summary_raw)

            return ExperienceLeaf(
                node_id="",  # merge_leaves 会分配新 ID
                experience_id="",
                path=base.path,
                label=base.label,
                node_type="case",
                task_description=data.get("task_description", base.task_description),
                domain_keywords=base.domain_keywords,
                skill_used=base.skill_used,
                steps_summary=steps_summary,
                pitfalls=data.get("pitfalls", []),
                solutions=data.get("solutions", []),
                code_snippets=data.get("code_snippets", []),
                final_outcome=data.get("final_outcome", "success"),
                session_id="merged",
                created_at=datetime.now().isoformat(),
                importance=float(data.get("importance", max(l.importance for l in group))),
            )
        except Exception as e:
            logger.error(f"LLM 合并经验失败: {e}")
            # fallback: 取最完整的叶子
            best = max(group, key=lambda l: (len(l.pitfalls) + len(l.solutions), l.importance))
            merged_pitfalls = list(dict.fromkeys(p for l in group for p in l.pitfalls))
            merged_solutions = list(dict.fromkeys(s for l in group for s in l.solutions))
            best.pitfalls = merged_pitfalls
            best.solutions = merged_solutions
            best.importance = max(l.importance for l in group)
            return best

class TaskExperienceExtractor:
    """LLM 驱动的经验提取器"""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    def _should_capture(
        self,
        plan: Optional[str],
        tool_results: List[Dict],
        had_errors: bool,
        execution_duration: float,
    ) -> bool:
        if not plan:
            return False
        if had_errors:
            return True
        if execution_duration > 30:
            return True
        if len(tool_results) < settings.task_experience.min_tool_calls_for_capture:
            return False
        return True

    def extract(
        self,
        user_input: str,
        plan: Optional[str],
        tool_results: List[Dict],
        final_output: str,
        had_errors: bool,
        execution_duration: float = 0.0,
    ) -> Optional[ExperienceLeaf]:
        if not self._should_capture(plan, tool_results, had_errors, execution_duration):
            return None

        try:
            prompt = EXTRACTION_PROMPT.format(
                user_input=user_input[:500],
                plan_summary=str(plan)[:500] if plan else "",
                tool_calls_summary=self._summarize_tool_calls(tool_results)[:1000],
                final_output_summary=str(final_output)[:500],
                had_errors=str(had_errors),
                duration_seconds=int(execution_duration),
            )

            response = self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500,
            )

            return self._parse_response(response, user_input)

        except Exception as e:
            logger.error(f"经验提取失败: {e}")
            return None

    def _summarize_tool_calls(self, tool_results: list) -> str:
        summaries = []
        for tr in tool_results[:10]:
            # tool_results 可能是 ToolResult 对象或 dict
            if hasattr(tr, 'name'):
                name = tr.name
                result = str(tr.content)[:100]
            elif isinstance(tr, dict):
                name = tr.get("tool_name", tr.get("name", "unknown"))
                result = str(tr.get("result", tr.get("output", "")))[:100]
            else:
                name = "unknown"
                result = ""
            summaries.append(f"{name}: {result}")
        return "; ".join(summaries)

    def _parse_response(self, response, original_input: str) -> Optional[ExperienceLeaf]:
        try:
            raw = response.content if hasattr(response, 'content') else str(response)
            text = raw.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)

            if not data or not data.get("tree_path"):
                return None

            tree_path = data.get("tree_path", [])
            if not tree_path:
                return None

            steps_summary_raw = data.get("steps_summary", "")
            steps_summary = "; ".join(steps_summary_raw) if isinstance(steps_summary_raw, list) else str(steps_summary_raw)

            return ExperienceLeaf(
                node_id="",
                experience_id="",
                path=tree_path,
                label=tree_path[-1] if tree_path else "",
                node_type="case",
                task_description=data.get("task_description", ""),
                domain_keywords=data.get("domain_keywords", []),
                skill_used=data.get("skill_used", ""),
                steps_summary=steps_summary,
                pitfalls=data.get("pitfalls", []),
                solutions=data.get("solutions", []),
                code_snippets=data.get("code_snippets", []),
                final_outcome=data.get("final_outcome", "success"),
                session_id="auto",
                created_at=datetime.now().isoformat(),
                importance=float(data.get("importance", 0.5)),
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"解析经验提取结果失败: {e}")
            return None


# ── TaskExperienceCapture ──────────────────────────────────────

class TaskExperienceCapture:
    """自动捕获编排器 — 任务完成后后台提取经验"""

    _instance: Optional['TaskExperienceCapture'] = None

    def __init__(self, llm_service=None):
        self.llm_service = llm_service
        self._extractor = TaskExperienceExtractor(llm_service) if llm_service else None

    def on_task_complete(
        self,
        session_id: str,
        user_input: str,
        plan: Optional[str],
        tool_results: List[Dict],
        final_output: str,
        execution_duration: float = 0.0,
        had_errors: bool = False,
    ) -> None:
        if not settings.task_experience.enabled or not settings.task_experience.auto_capture:
            return
        if not self._extractor:
            return

        def _capture():
            try:
                leaf = self._extractor.extract(
                    user_input=user_input,
                    plan=plan,
                    tool_results=tool_results,
                    final_output=final_output,
                    had_errors=had_errors,
                    execution_duration=execution_duration,
                )
                if leaf:
                    store = get_task_experience_store()
                    if self.llm_service and not store._llm_service:
                        store.set_llm_service(self.llm_service)
                    tree_path = leaf.path
                    store.record_experience(leaf, tree_path)
                    logger.info(f"任务经验自动捕获成功: {leaf.path}")
                else:
                    logger.debug("任务经验提取结果为空，跳过记录")
            except Exception as e:
                logger.error(f"任务经验自动捕获失败: {e}")

        t = threading.Thread(target=_capture, daemon=True, name="experience-capture")
        t.start()


# ── 工厂函数 ──────────────────────────────────────────────────

def get_task_experience_store() -> TaskExperienceStore:
    return TaskExperienceStore()


def get_task_experience_capture(llm_service=None) -> TaskExperienceCapture:
    if TaskExperienceCapture._instance is None:
        TaskExperienceCapture._instance = TaskExperienceCapture(llm_service=llm_service)
    elif llm_service and not TaskExperienceCapture._instance.llm_service:
        TaskExperienceCapture._instance.llm_service = llm_service
        TaskExperienceCapture._instance._extractor = TaskExperienceExtractor(llm_service)
    return TaskExperienceCapture._instance
