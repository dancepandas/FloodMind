"""
经验树索引管理

提供树状层级结构的经验组织能力。
经验按 领域→子领域→任务类型→具体案例 逐层组织，
形成一棵可生长的"经验树"。

树索引以 JSON 文件持久化，支持关键词检索。
支持渐进压缩：叶子超过阈值时生成摘要节点（SummaryNode），
上下文注入只给摘要而非全部叶子。
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExperienceNode:
    """树节点 — 领域/子领域/任务类型的分支节点"""

    node_id: str
    path: List[str]
    label: str
    node_type: str  # "domain" | "task_type" | "case"
    children_ids: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExperienceNode':
        return cls(**data)


@dataclass
class ExperienceLeaf(ExperienceNode):
    """叶子节点 — 具体任务执行案例"""

    node_type: str = "case"
    experience_id: str = ""
    task_description: str = ""
    domain_keywords: List[str] = field(default_factory=list)
    skill_used: str = ""
    steps_summary: str = ""
    pitfalls: List[str] = field(default_factory=list)
    solutions: List[str] = field(default_factory=list)
    code_snippets: List[str] = field(default_factory=list)
    final_outcome: str = "success"
    session_id: str = ""
    importance: float = 0.5
    hit_count: int = 0
    last_hit_at: str = ""
    updated_at: str = ""
    success_count: int = 0      # 经验被引用后任务成功的次数
    failure_count: int = 0      # 经验被引用后任务失败的次数
    base_importance: float = 0.5  # LLM 评的基础重要性，recompute_importance 保持此值不变

    def patch(self, **kwargs) -> None:
        """增量更新字段，只修改传入的非空值"""
        updatable = (
            "task_description", "steps_summary", "pitfalls",
            "solutions", "code_snippets", "final_outcome",
            "importance", "domain_keywords", "skill_used",
        )
        for k, v in kwargs.items():
            if k in updatable and v:
                setattr(self, k, v)
        self.updated_at = datetime.now().isoformat()

    def recompute_importance(self) -> float:
        """综合基础重要性 + 反馈信号 + 热度 重新计算 importance"""
        total = self.success_count + self.failure_count
        feedback_score = (self.success_count / total) if total > 0 else 0.5
        hit_norm = min(self.hit_count / 20.0, 1.0)
        self.importance = 0.4 * self.base_importance + 0.3 * feedback_score + 0.3 * hit_norm
        return self.importance

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExperienceLeaf':
        # 兼容旧数据：缺少热度字段时给默认值
        data.setdefault("hit_count", 0)
        data.setdefault("last_hit_at", "")
        data.setdefault("updated_at", "")
        data.setdefault("success_count", 0)
        data.setdefault("failure_count", 0)
        data.setdefault("base_importance", data.get("importance", 0.5))
        # 兼容 LLM 返回 list 类型：steps_summary 应为 str
        if isinstance(data.get("steps_summary"), list):
            data["steps_summary"] = "; ".join(data["steps_summary"])
        return cls(**data)

    def to_searchable_text(self) -> str:
        """生成用于检索的文本"""
        parts = []
        if self.task_description:
            parts.append(f"任务描述: {self.task_description}")
        if self.pitfalls:
            parts.append("坑点: " + "; ".join(self.pitfalls))
        if self.solutions:
            parts.append("解决方案: " + "; ".join(self.solutions))
        if self.steps_summary:
            parts.append(f"步骤摘要: {self.steps_summary}")
        if self.skill_used:
            parts.append(f"使用skill: {self.skill_used}")
        if self.code_snippets:
            parts.append(f"可复用代码: {len(self.code_snippets)}个片段")
        parts.append(f"结果: {self.final_outcome}")
        return "\n".join(parts)

    def to_markdown(self) -> str:
        """渲染为可读 Markdown"""
        path_str = "/".join(self.path)
        lines = [
            f"### {self.label}",
            f"路径: {path_str}",
            f"任务描述: {self.task_description}",
        ]
        if self.skill_used:
            lines.append(f"使用skill: {self.skill_used}")
        if self.pitfalls:
            lines.append("**坑点:**")
            for p in self.pitfalls:
                lines.append(f"  - {p}")
        if self.solutions:
            lines.append("**解决方案:**")
            for s in self.solutions:
                lines.append(f"  - {s}")
        if self.steps_summary:
            lines.append(f"**步骤摘要:** {self.steps_summary}")
        if self.code_snippets:
            lines.append("**可复用代码:**")
            for i, code in enumerate(self.code_snippets, 1):
                lines.append(f"  片段{i}:")
                for code_line in code.split("\n")[:10]:
                    lines.append(f"    {code_line}")
                if code.count("\n") > 10:
                    lines.append(f"    ... (共{code.count(chr(10))+1}行)")
        lines.append(f"结果: {self.final_outcome} | 重要性: {self.importance}")
        lines.append(f"记录时间: {self.created_at}")
        return "\n".join(lines)


@dataclass
class SummaryNode:
    """摘要节点 — 对某个子树下所有叶子的渐进压缩"""

    node_id: str
    tree_path: List[str]
    summary_text: str
    child_ids: List[str] = field(default_factory=list)
    sealed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    hit_count: int = 0
    last_hit_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SummaryNode':
        data.setdefault("hit_count", 0)
        data.setdefault("last_hit_at", "")
        return cls(**data)


SUMMARY_PROMPT = """请将以下多条任务执行经验压缩为一段简洁摘要（≤200字），保留关键坑点、核心解决方案和重要模式，去除冗余细节。

## 经验列表
{experiences}

## 输出要求
- 用中文输出
- 突出最常见的坑点和最有效的解决方案
- 标注成功率（如：3/5成功）
- 不要逐条列举，要归纳共性"""


class ExperienceTree:
    """经验树索引管理器

    管理树状层级结构，支持：
    - 按路径查找/创建节点
    - 子树渲染为 Markdown
    - JSON 持久化
    - 渐进压缩（摘要 seal）
    - 热度衰减
    """

    ROOT_ID = "root"

    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self._index_file = os.path.join(persist_dir, "tree_index.json")
        self._nodes: Dict[str, ExperienceNode] = {}
        self._leaves: Dict[str, ExperienceLeaf] = {}
        self._summaries: Dict[str, SummaryNode] = {}  # key = "/".join(tree_path)
        self._archived: Dict[str, ExperienceLeaf] = {}  # 归档叶子，不参与搜索和注入
        self._lock = threading.RLock()  # 可重入锁，防止递归方法死锁
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._index_file):
            root = ExperienceNode(
                node_id=self.ROOT_ID,
                path=["经验树根"],
                label="经验树根",
                node_type="domain",
            )
            self._nodes[self.ROOT_ID] = root
            self._save()
            logger.info("经验树索引初始化完成（新树）")
            return

        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for node_data in data.get("nodes", []):
                node_type = node_data.get("node_type", "domain")
                if node_type == "case":
                    leaf = ExperienceLeaf.from_dict(node_data)
                    self._leaves[leaf.node_id] = leaf
                    self._nodes[leaf.node_id] = leaf
                else:
                    node = ExperienceNode.from_dict(node_data)
                    self._nodes[node.node_id] = node

            for summary_data in data.get("summaries", []):
                summary = SummaryNode.from_dict(summary_data)
                key = "/".join(summary.tree_path)
                self._summaries[key] = summary

            for archived_data in data.get("archived", []):
                leaf = ExperienceLeaf.from_dict(archived_data)
                self._archived[leaf.node_id] = leaf

            logger.info(
                f"经验树索引加载完成: {len(self._nodes)} 个节点, "
                f"{len(self._leaves)} 个叶子案例, "
                f"{len(self._summaries)} 个摘要"
            )
        except Exception as e:
            logger.error(f"经验树索引加载失败: {e}")
            root = ExperienceNode(
                node_id=self.ROOT_ID,
                path=["经验树根"],
                label="经验树根",
                node_type="domain",
            )
            self._nodes[self.ROOT_ID] = root

    def _save(self) -> None:
        with self._lock:
            try:
                nodes_data = [node.to_dict() for node in self._nodes.values()]
                summaries_data = [s.to_dict() for s in self._summaries.values()]

                data = {
                    "updated_at": datetime.now().isoformat(),
                    "nodes": nodes_data,
                    "summaries": summaries_data,
                    "archived": [leaf.to_dict() for leaf in self._archived.values()],
                }
                with open(self._index_file + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(self._index_file + ".tmp", self._index_file)
            except Exception as e:
                logger.error(f"经验树索引保存失败: {e}")

    def find_node(self, path: List[str]) -> Optional[ExperienceNode]:
        """按路径查找节点"""
        with self._lock:
            for node in self._nodes.values():
                if node.path == path:
                    return node
            return None

    def find_or_create_node(self, path: List[str], node_type: str = "domain") -> ExperienceNode:
        """按路径查找节点，不存在则创建整条路径"""
        with self._lock:
            existing = self.find_node(path)
            if existing:
                return existing

            created_nodes = []
            for i in range(len(path)):
                sub_path = path[:i + 1]
                node = self.find_node(sub_path)
                if node is None:
                    depth = i + 1
                    if depth == 1:
                        nt = "domain"
                    elif depth == len(path) and node_type == "case":
                        nt = "task_type"
                    elif depth == len(path):
                        nt = node_type
                    else:
                        nt = "task_type"

                    new_node = ExperienceNode(
                        node_id=str(uuid.uuid4()),
                        path=sub_path,
                        label=sub_path[-1],
                        node_type=nt,
                        parent_id=created_nodes[-1].node_id if created_nodes else self.ROOT_ID,
                    )
                    self._nodes[new_node.node_id] = new_node
                    created_nodes.append(new_node)

                    if new_node.parent_id and new_node.parent_id in self._nodes:
                        parent = self._nodes[new_node.parent_id]
                        if new_node.node_id not in parent.children_ids:
                            parent.children_ids.append(new_node.node_id)
                else:
                    created_nodes.append(node)

            self._save()
            return created_nodes[-1]

    def find_closest_node(self, keywords: List[str]) -> Optional[ExperienceNode]:
        """根据关键词匹配最近的节点"""
        with self._lock:
            best_node = None
            best_score = 0.0

            keyword_set = set(k.lower() for k in keywords)
            for node in self._nodes.values():
                if node.node_type == "case":
                    continue
                label_terms = set(node.label.lower().split())
                path_terms = set(p.lower() for p in node.path)
                score = len(keyword_set & (label_terms | path_terms)) / max(len(keyword_set), 1)
                if score > best_score:
                    best_score = score
                    best_node = node

            return best_node if best_score > 0 else None

    def get_children(self, node_id: str) -> List[ExperienceNode]:
        """获取子节点"""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return []
            return [
                self._nodes[cid]
                for cid in node.children_ids
                if cid in self._nodes
            ]

    def get_leaves(self, node_id: str) -> List[ExperienceLeaf]:
        """获取某节点下所有叶子案例（递归）"""
        with self._lock:
            leaves = []
            node = self._nodes.get(node_id)
            if not node:
                return []

            for cid in node.children_ids:
                child = self._nodes.get(cid)
                if not child:
                    continue
                if child.node_type == "case" and cid in self._leaves:
                    leaves.append(self._leaves[cid])
                else:
                    leaves.extend(self.get_leaves(cid))
            return leaves

    def get_path_str(self, node_id: str) -> str:
        """返回节点完整路径字符串"""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return ""
            return "/".join(node.path)

    def add_leaf(self, leaf: ExperienceLeaf, parent_path: List[str]) -> ExperienceLeaf:
        """添加叶子案例到指定父路径下"""
        with self._lock:
            parent = self.find_or_create_node(parent_path)
            leaf.parent_id = parent.node_id
            leaf.path = parent_path + [leaf.label]

            if not leaf.node_id:
                leaf.node_id = str(uuid.uuid4())
            if not leaf.experience_id:
                leaf.experience_id = leaf.node_id

            self._nodes[leaf.node_id] = leaf
            self._leaves[leaf.node_id] = leaf

            if leaf.node_id not in parent.children_ids:
                parent.children_ids.append(leaf.node_id)

            # 如果该路径已有摘要，追加到摘要的 child_ids
            summary_key = "/".join(parent_path)
            if summary_key in self._summaries:
                summary = self._summaries[summary_key]
                if leaf.node_id not in summary.child_ids:
                    summary.child_ids.append(leaf.node_id)

            self._save()
            logger.info(
                f"经验树添加叶子: path={leaf.path}, "
                f"outcome={leaf.final_outcome}, pitfalls={len(leaf.pitfalls)}"
            )
            return leaf

    def bump_hotness(self, node_id: str) -> None:
        """命中时更新热度"""
        with self._lock:
            leaf = self._leaves.get(node_id)
            if leaf:
                leaf.hit_count += 1
                leaf.last_hit_at = datetime.now().isoformat()
                leaf.recompute_importance()
                self._save()
                return

            summary_key = node_id
            summary = self._summaries.get(summary_key)
            if summary:
                summary.hit_count += 1
                summary.last_hit_at = datetime.now().isoformat()
                self._save()

    def seal_branch(self, path: List[str], summary_text: str) -> SummaryNode:
        """为指定路径创建摘要节点"""
        with self._lock:
            node = self.find_node(path)
            if not node:
                raise ValueError(f"路径不存在: {path}")

            leaves = self.get_leaves(node.node_id)
            child_ids = [leaf.node_id for leaf in leaves]

            key = "/".join(path)
            summary = SummaryNode(
                node_id=str(uuid.uuid4()),
                tree_path=path,
                summary_text=summary_text,
                child_ids=child_ids,
                sealed_at=datetime.now().isoformat(),
            )
            self._summaries[key] = summary
            self._save()
            logger.info(f"经验树 seal: path={path}, {len(child_ids)} 个叶子")
            return summary

    def get_summary(self, path: List[str]) -> Optional[SummaryNode]:
        """获取某路径下的摘要"""
        with self._lock:
            key = "/".join(path)
            return self._summaries.get(key)

    def get_all_summaries(self) -> List[SummaryNode]:
        """获取所有摘要"""
        with self._lock:
            return list(self._summaries.values())

    def drill_down(self, summary_node_id: str) -> List[ExperienceLeaf]:
        """从摘要展开到叶子（支持 node_id 或路径 key）"""
        with self._lock:
            summary = self._summaries.get(summary_node_id)
            if not summary:
                for s in self._summaries.values():
                    if s.node_id == summary_node_id:
                        summary = s
                        break
            if not summary:
                return []
            return [self._leaves[cid] for cid in summary.child_ids if cid in self._leaves]

    def get_branches_needing_seal(self, threshold: int) -> List[tuple]:
        """返回需要 seal 的分支列表：[(path, leaf_count), ...]"""
        with self._lock:
            result = []
            for node in self._nodes.values():
                if node.node_type == "case":
                    continue
                if node.node_id == self.ROOT_ID:
                    continue
                key = "/".join(node.path)
                if key in self._summaries:
                    continue
                leaves = self.get_leaves(node.node_id)
                if len(leaves) >= threshold:
                    result.append((node.path, len(leaves)))
            return result

    def render_tree_markdown(self) -> str:
        """渲染整棵树为 Markdown"""
        with self._lock:
            return self._render_node(self.ROOT_ID, indent=0)

    def render_subtree_markdown(self, node_id: str) -> str:
        """渲染子树为 Markdown"""
        with self._lock:
            return self._render_node(node_id, indent=0)

    def render_path_markdown(self, path_str: str) -> str:
        """按路径字符串渲染子树"""
        path = [p.strip() for p in path_str.split("/") if p.strip()]
        if not path:
            return self.render_tree_markdown()

        node = self.find_node(path)
        if not node:
            return f"未找到路径: {path_str}"
        return self.render_subtree_markdown(node.node_id)

    def render_summary_markdown(self) -> str:
        """渲染上下文注入内容：摘要 + 未摘要分支的关键细节"""
        with self._lock:
            lines = ["[经验摘要]"]

            # 已摘要分支：显示摘要文本
            for key, summary in sorted(self._summaries.items()):
                path_str = "/".join(summary.tree_path)
                lines.append(f"## {path_str}")
                lines.append(summary.summary_text)
                lines.append(f"({len(summary.child_ids)}条经验, 命中{summary.hit_count}次)")
                lines.append("")

            # 未摘要分支：显示关键坑点和解决方案
            summarized_paths = set("/".join(s.tree_path) for s in self._summaries.values())
            for node in self._nodes.values():
                if node.node_type == "case" or node.node_id == self.ROOT_ID:
                    continue
                key = "/".join(node.path)
                if key in summarized_paths:
                    continue
                leaves = self.get_leaves(node.node_id)
                if not leaves:
                    continue
                lines.append(f"## {key} ({len(leaves)}条经验)")
                # 提取关键坑点和解决方案
                all_pitfalls = []
                all_solutions = []
                for leaf in leaves:
                    all_pitfalls.extend(leaf.pitfalls)
                    all_solutions.extend(leaf.solutions)
                if all_pitfalls:
                    unique_pitfalls = list(dict.fromkeys(all_pitfalls))[:3]
                    lines.append("坑点: " + "; ".join(unique_pitfalls))
                if all_solutions:
                    unique_solutions = list(dict.fromkeys(all_solutions))[:3]
                    lines.append("方案: " + "; ".join(unique_solutions))
                lines.append("")

            return "\n".join(lines)

    def _render_browse_tree(self, node_id: str, indent: int) -> str:
        """渲染浏览视图：只显示结构 + 叶子数，不展开叶子详情"""
        node = self._nodes.get(node_id)
        if not node:
            return ""

        prefix = "  " * indent
        lines = []

        if node.node_type == "case" and node_id in self._leaves:
            leaf = self._leaves[node_id]
            lines.append(f"{prefix}├── {leaf.label} [{leaf.final_outcome}]")
        else:
            children = self.get_children(node_id)
            leaf_count = sum(1 for c in children if c.node_type == "case" and c.node_id in self._leaves)
            branch_count = len(children) - leaf_count

            key = "/".join(node.path) if node_id != self.ROOT_ID else ""
            summary = self._summaries.get(key) if key else None

            if node_id == self.ROOT_ID:
                lines.append("经验树")
            else:
                suffix = ""
                if leaf_count > 0:
                    suffix += f" ({leaf_count}条经验)"
                if branch_count > 0:
                    suffix += f" ({branch_count}个子类)"
                if summary:
                    suffix += " [已摘要]"
                lines.append(f"{prefix}├── {node.label}{suffix}")

            for child in children:
                lines.append(self._render_browse_tree(child.node_id, indent + 1))

        return "\n".join(lines)

    def _render_node(self, node_id: str, indent: int) -> str:
        """递归渲染节点"""
        node = self._nodes.get(node_id)
        if not node:
            return ""

        prefix = "  " * indent
        lines = []

        if node.node_type == "case" and node_id in self._leaves:
            leaf = self._leaves[node_id]
            lines.append(f"{prefix}├── {leaf.label} [{leaf.final_outcome}]")
            if leaf.pitfalls:
                for p in leaf.pitfalls[:2]:
                    lines.append(f"{prefix}│     坑点: {p}")
            if leaf.solutions:
                for s in leaf.solutions[:2]:
                    lines.append(f"{prefix}│     方案: {s}")
        else:
            children = self.get_children(node_id)
            leaf_count = sum(1 for c in children if c.node_type == "case" and c.node_id in self._leaves)
            branch_count = len(children) - leaf_count

            if node_id == self.ROOT_ID:
                lines.append("经验树")
            else:
                suffix = ""
                if leaf_count > 0:
                    suffix += f" ({leaf_count}条经验)"
                if branch_count > 0:
                    suffix += f" ({branch_count}个子类)"
                lines.append(f"{prefix}├── {node.label}{suffix}")

            for child in children:
                lines.append(self._render_node(child.node_id, indent + 1))

        return "\n".join(lines)

    def get_all_leaves(self) -> List[ExperienceLeaf]:
        """获取所有叶子案例"""
        with self._lock:
            return list(self._leaves.values())

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            branch_count = sum(1 for n in self._nodes.values() if n.node_type != "case")
            leaf_count = len(self._leaves)
            return {
                "total_nodes": len(self._nodes),
                "branch_nodes": branch_count,
                "leaf_cases": leaf_count,
                "summaries": len(self._summaries),
                "root_children": len(self._nodes[self.ROOT_ID].children_ids),
                "archived": len(self._archived),
            }

    # ── 巡检支持：去重、合并、归档、删除 ────────────────────────

    @staticmethod
    def _split_words(text: str):
        """分词：按空格分割 + 对中文逐字切分"""
        import re
        tokens = []
        for part in text.split():
            if re.search(r'[\u4e00-\u9fff]', part):
                tokens.extend(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', part))
            else:
                tokens.append(part)
        return tokens

    def find_duplicate_groups(self, similarity_threshold: float = 0.8) -> List[List[ExperienceLeaf]]:
        """查找相似叶子组（Jaccard 词集合相似度 > threshold）

        返回每组 >= 2 个叶子的列表，用于后续合并。
        """
        with self._lock:
            leaves = list(self._leaves.values())
            if len(leaves) < 2:
                return []

            # 为每个叶子构建词集合
            word_sets: Dict[str, set] = {}
            for leaf in leaves:
                words = set()
                for text in [leaf.task_description, leaf.steps_summary]:
                    words.update(w.lower() for w in self._split_words(text) if len(w) > 1)
                for text in leaf.pitfalls + leaf.solutions:
                    words.update(w.lower() for w in self._split_words(text) if len(w) > 1)
                for kw in leaf.domain_keywords:
                    words.add(kw.lower())
                word_sets[leaf.node_id] = words

            # Union-Find 分组
            parent: Dict[str, str] = {leaf.node_id: leaf.node_id for leaf in leaves}

            def find(x: str) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: str, b: str) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for i in range(len(leaves)):
                for j in range(i + 1, len(leaves)):
                    set_a = word_sets[leaves[i].node_id]
                    set_b = word_sets[leaves[j].node_id]
                    if not set_a or not set_b:
                        continue
                    intersection = len(set_a & set_b)
                    union_size = len(set_a | set_b)
                    if union_size > 0 and intersection / union_size >= similarity_threshold:
                        union(leaves[i].node_id, leaves[j].node_id)

            # 收集分组
            groups: Dict[str, List[ExperienceLeaf]] = {}
            for leaf in leaves:
                root = find(leaf.node_id)
                groups.setdefault(root, []).append(leaf)

            return [g for g in groups.values() if len(g) >= 2]

    def remove_leaf(self, node_id: str) -> bool:
        """彻底删除叶子节点"""
        with self._lock:
            leaf = self._leaves.get(node_id)
            if not leaf:
                return False

            # 从父节点 children_ids 中移除
            if leaf.parent_id and leaf.parent_id in self._nodes:
                parent = self._nodes[leaf.parent_id]
                parent.children_ids = [cid for cid in parent.children_ids if cid != node_id]

            # 从摘要 child_ids 中移除
            for summary in self._summaries.values():
                summary.child_ids = [cid for cid in summary.child_ids if cid != node_id]

            del self._leaves[node_id]
            del self._nodes[node_id]
            self._save()
            logger.info(f"经验树删除叶子: node_id={node_id}, path={leaf.path}")
            return True

    def update_leaf(self, node_id: str, **kwargs) -> bool:
        """增量更新叶子节点字段"""
        with self._lock:
            leaf = self._leaves.get(node_id)
            if not leaf:
                return False
            leaf.patch(**kwargs)
            self._save()
            logger.info(f"经验树更新叶子: node_id={node_id}, fields={list(kwargs.keys())}")
            return True

    def archive_leaf(self, node_id: str) -> bool:
        """归档叶子：从活跃列表移到归档列表，不参与搜索和上下文注入"""
        with self._lock:
            leaf = self._leaves.get(node_id)
            if not leaf:
                return False

            # 从父节点 children_ids 中移除
            if leaf.parent_id and leaf.parent_id in self._nodes:
                parent = self._nodes[leaf.parent_id]
                parent.children_ids = [cid for cid in parent.children_ids if cid != node_id]

            # 从摘要 child_ids 中移除
            for summary in self._summaries.values():
                summary.child_ids = [cid for cid in summary.child_ids if cid != node_id]

            # 移到归档
            del self._leaves[node_id]
            self._archived[node_id] = leaf
            self._save()
            logger.info(f"经验树归档叶子: node_id={node_id}, path={leaf.path}")
            return True

    def merge_leaves(self, old_ids: List[str], merged: ExperienceLeaf, parent_path: List[str]) -> ExperienceLeaf:
        """合并多个旧叶子为一个新叶子，删除旧叶子"""
        with self._lock:
            # 删除旧叶子
            for old_id in old_ids:
                leaf = self._leaves.get(old_id)
                if leaf:
                    if leaf.parent_id and leaf.parent_id in self._nodes:
                        parent = self._nodes[leaf.parent_id]
                        parent.children_ids = [cid for cid in parent.children_ids if cid != old_id]
                    for summary in self._summaries.values():
                        summary.child_ids = [cid for cid in summary.child_ids if cid != old_id]
                    del self._leaves[old_id]
                    del self._nodes[old_id]

            # 添加合并后的新叶子
            return self.add_leaf(merged, parent_path)

    def get_archived_leaves(self) -> List[ExperienceLeaf]:
        """获取所有归档叶子"""
        with self._lock:
            return list(self._archived.values())
