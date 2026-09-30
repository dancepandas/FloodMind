"""记忆服务门面（P0）：装配与生命周期的唯一入口。

三服务（recall/consolidation/core）由本门面懒加载单例装配。
门面是记忆子系统的唯一装配点——所有接线方（native_flood_agent 注入、
background_review 回顾、memory_tools 工具）只依赖本模块；
未来要替换任一服务的构造方式（换存储后端、换检索实现）只改这里。
on_run_completed / on_checkpoint 为 P1/P2 预留钩子，本次空实现。
"""

import logging
import threading
from typing import Any, Optional

from floodmind.config.settings import settings

logger = logging.getLogger(__name__)


class MemoryServices:
    """门面：持有四个记忆子系统实例并统一对外暴露生命周期钩子。

    属性说明：
        store:         语义事实存储（Graphiti 式失效语义），四个服务共享的数据底座；
        recall:        三因子召回服务，构建 system 注入块（disabled 时为 None）；
        consolidation: 四类操作固化管线（ADD/UPDATE/DELETE/NOOP 判定）；
        core:          项目级 core memory（常驻 system 注入）。

    装配边界：本类是唯一装配点；要替换任一服务的实现（存储后端、检索
    策略）仅重写 __init__ 内的构造过程，对外属性与钩子签名保持不变。
    """

    def __init__(self):
        # 延迟导入子服务模块，避免 settings/config 循环依赖链在模块加载期爆炸。
        from floodmind.memory.consolidation import MemoryConsolidationService
        from floodmind.memory.core_memory import CoreMemoryStore
        from floodmind.memory.recall import MemoryRecallService
        from floodmind.memory.semantic_store import SemanticMemoryStore

        # ── 数据底座：语义事实存储（所有服务共享）──────────────────────────
        self.store = SemanticMemoryStore()
        # 一次性导入旧长期记忆（幂等：按 content 去重），此后旧 store 冻结只读
        try:
            self.store.import_legacy()
        except Exception as e:
            logger.warning("[记忆门面] 旧长期记忆导入失败（非致命）: %s", e)

        # ── 召回服务：配置启用时装配，disabled 时属性为 None（调用方需判空）──
        rc = settings.memory_recall
        self.recall: Optional[MemoryRecallService] = (
            MemoryRecallService(
                self.store,
                top_k=rc.top_k,
                min_score=rc.min_score,
                decay_scale_days=rc.decay_scale_days,
                max_chars=rc.max_chars,
            ) if rc.enabled else None
        )

        # ── 固化管线：配置启用时装配；模型客户端由 background_review 注入 ──
        kc = settings.memory_consolidation
        self.consolidation: Optional[MemoryConsolidationService] = (
            MemoryConsolidationService(
                self.store,
                conflict_candidate_top_k=kc.conflict_candidate_top_k,
            ) if kc.enabled else None
        )

        # ── 项目级 core memory：配置启用时装配（含旧会话文件聚合迁移）─────
        cc = settings.core_memory
        self.core: Optional[CoreMemoryStore] = (
            CoreMemoryStore(migrate_legacy=cc.migrate_legacy, max_chars=cc.max_chars)
            if cc.enabled else None
        )

    def on_turn_completed(self, review_result: Any) -> None:
        """每轮回顾完成后：偏好+facts 交给四类操作管线判定。

        review_result 约定（与 background_review.ReviewResult 对齐）：
        user_preferences / facts 均为 [{"content": str, "type": str}] 列表。
        consolidation 为 None（配置禁用）或 review_result 为 None 时直接返回；
        无模型客户端时管线判定失败全丢弃（不回退直写），保证语义事实的唯一入口。
        """
        if self.consolidation is None or review_result is None:
            return
        # 偏好与 facts 统一规整为候选条目，type 缺省分别回退 preference / fact
        candidates = [
            {"content": p.get("content", ""), "type": p.get("type", "preference")}
            for p in getattr(review_result, "user_preferences", [])
        ]
        candidates += [
            {"content": f.get("content", ""), "type": f.get("type", "fact")}
            for f in getattr(review_result, "facts", [])
        ]
        if candidates:
            self.consolidation.consolidate(candidates)

    def on_run_completed(self, run_summary: Any = None) -> None:
        """P1 会话结束沉淀预留——本次空实现。"""
        return None

    def on_checkpoint(self, checkpoint_ref: Any = None) -> None:
        """P2 记忆演化维护预留——本次空实现。"""
        return None


# ── 线程安全懒加载单例 ───────────────────────────────────────────────────────
# 单例双重保障：_lock 串行化首次构造；_instance 为模块级可 monkeypatch 的注入点
_instance: Optional[MemoryServices] = None
_lock = threading.Lock()


def get_memory_services() -> MemoryServices:
    """获取门面单例；首次调用时加锁构造，后续直接返回。

    接线方（native_flood_agent / memory_tools / background_review 传递路径）
    一律通过本函数访问门面；门面实现可整体替换而不影响调用方。
    """
    global _instance
    with _lock:
        if _instance is None:
            _instance = MemoryServices()
        return _instance
