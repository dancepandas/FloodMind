"""语义事实主动注入（P0-1）。

三因子打分（Generative Agents 风格）：score = relevance × recency × importance
- relevance：查询词与条目内容的关键词重叠率
- recency：MemoryBank 艾宾浩斯衰减 exp(-Δt / S)，S=decay_scale_days
- importance：条目 importance 字段（clamp 到 [0,1]）

设计依据与边界说明：
- 三因子模型出处：Generative Agents（Park et al., 2023）的
  observation 检索排序，recency/importance/relevance 三者相乘；
  recency 的指数衰减形式取自 MemoryBank（Zhong et al., 2023）的
  艾宾浩斯遗忘曲线近似 exp(-Δt/S)，S 为记忆强度尺度（天）。
- relevance 分母取 len(query_terms) 而非交集/并集：查询视角的
  覆盖率，保证"查询词全部命中"时 relevance=1（测试
  test_score_monotonic_decay 依赖该口径）。
- 为何不缓存：LTM 为百级条目，extract_terms 为正则扫描 +
  集合交并，单次打分为毫秒级；且 build_block 每轮装配
  system prompt 时调用，条目可能刚被 add/mark_deleted，
  缓存会引入失效一致性问题。故每轮全量重算，不做缓存
  （P1 换 embedding 向量召回后再加 TTL 缓存）。
- 空输入/空查询词/无 active 条目一律返回 ""（不注入），
  时间解析失败按 Δt=0（最新）处理，不打断召回。
- 预算截断：按打分顺序从高到低追加条目行，单行放不下
  max_chars 余量即整体 break（不做单条内容截断，避免
  截出语义残句注入 prompt）。
"""

import math
from datetime import datetime
from typing import List, Optional, Set, Tuple

from floodmind.memory.semantic_store import LTMEntryV2, SemanticMemoryStore, extract_terms

# 注：本模块刻意不持有 logger——召回是每轮打分的热路径，全程无日志输出点，
# 保留未使用的 logger 属死代码（终审清理项）。


class MemoryRecallService:
    """语义事实召回服务：对 active 条目做三因子打分，渲染注入块。

    意图：在门面层装配 system prompt 前，按当前用户输入捞取
    top_k 条相关历史事实，包装为 "[长期记忆]" 文本块。
    边界条件：
    - build_block 空输入 / 无查询词 / 无 active 条目 / 全部低于
      min_score / 首行即超预算时返回 ""；
    - decay_scale_days 防御下限 1e-6，避免除零导致 ZeroDivisionError；
    - 仅消费 SemanticMemoryStore 的 active_entries()，天然过滤
      superseded/deleted 条目。
    """

    def __init__(
        self,
        store: SemanticMemoryStore,
        *,
        top_k: int = 5,
        min_score: float = 0.05,
        decay_scale_days: float = 30.0,
        max_chars: int = 1500,
    ):
        # store 仅用于读路径；参数全部可用关键字覆盖，便于测试与门面装配
        self._store = store
        self._top_k = top_k
        self._min_score = min_score
        self._decay_scale_days = max(float(decay_scale_days), 1e-6)
        self._max_chars = max_chars

    @staticmethod
    def score(
        entry: LTMEntryV2,
        query_terms: Set[str],
        now: datetime,
        decay_scale_days: float,
    ) -> float:
        """三因子乘积打分（relevance × recency × importance）。

        静态方法便于脱离服务实例单测。边界条件：
        - query_terms 为空 → 0.0（无查询词谈不上相关）；
        - 与条目内容零重叠 → 0.0（早退，省掉时间解析）；
        - entry.valid_at 非 ISO 格式或为空 → 按 Δt=0 处理
          （视为最新、recency=1），不抛异常打断召回；
        - now 早于 valid_at（时钟回拨/未来时间戳）→ Δt 截断为 0；
        - importance 越界输入 clamp 到 [0,1]。
        """
        if not query_terms:
            return 0.0
        entry_terms = extract_terms(entry.content)
        relevance = len(query_terms & entry_terms) / len(query_terms)
        if relevance <= 0:
            return 0.0
        try:
            valid_at = datetime.fromisoformat(entry.valid_at)
            delta_days = max((now - valid_at).total_seconds(), 0.0) / 86400.0
        except (ValueError, TypeError):
            delta_days = 0.0  # 时间缺失按最新处理，不打断召回
        # 艾宾浩斯衰减：Δt 每过一个尺度 S，得分降 1/e
        recency = math.exp(-delta_days / max(float(decay_scale_days), 1e-6))
        importance = min(max(float(entry.importance), 0.0), 1.0)
        return relevance * recency * importance

    def build_block(self, user_input: str, now: Optional[datetime] = None) -> str:
        """按当前输入对 active 条目打分取 top-k，渲染为 ContextBlock 文本；无命中返回 ""。

        流程：前置空检查 → 抽取查询词 → 全量打分 → min_score 过滤 →
        降序排序 → 按 max_chars 预算逐行追加 → 拼块。
        预算语义：头行固定计入预算；某行放不下时直接 break
        （截断是"行级"而非"字符级"，保证注入的事实都是完整句子）。
        """
        if not user_input or self._store.count_active() == 0:
            return ""
        now = now or datetime.now()
        terms = extract_terms(user_input)
        if not terms:
            return ""
        scored: List[Tuple[float, LTMEntryV2]] = [
            (self.score(e, terms, now, self._decay_scale_days), e)
            for e in self._store.active_entries()
        ]
        scored = [(s, e) for s, e in scored if s >= self._min_score]
        if not scored:
            return ""
        scored.sort(key=lambda x: x[0], reverse=True)

        lines = ["[长期记忆] 与当前请求可能相关的历史事实（已按相关度排序）："]
        total = len(lines[0])
        for s, e in scored[: self._top_k]:
            item = f"- ({e.category}) {e.content}"
            if total + len(item) > self._max_chars:
                break
            lines.append(item)
            total += len(item) + 1  # +1 为后续 join 时 "\n" 占的字符
        if len(lines) == 1:
            return ""
        return "\n".join(lines)
