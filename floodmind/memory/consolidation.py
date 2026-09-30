"""四类操作固化管线（P0-2，Mem0 式抽取 + Graphiti 式失效）。

管线分两个阶段：

- 阶段一（判定 + 验证）：先过滤空内容候选；无候选时不调 LLM 直接返回全零。
  为每条候选抽取关键词（extract_terms），用 SemanticMemoryStore.find_candidates
  取 top-k 冲突候选，连同候选列表一次性交给 LLM 做批量判定
  （ADD / UPDATE / DELETE / NOOP）。随后统一校验：数量一致、action 合法、
  index 在界内、UPDATE/DELETE 的 target_id 必须指向已存在的条目。
- 阶段二（统一应用）：全部通过才落盘——ADD 直接新增；UPDATE 先写入新条目再
  对旧条目做失效标记（mark_superseded，保留 superseded_by 回溯链）；
  DELETE 软删（mark_deleted，内容保留可回滚）；NOOP 仅计数。

丢弃回退的 rationale（铁律）：判定失败 / 解析失败 / 非法 action / 目标不存在
时，本轮全部候选丢弃不写入（stats["discarded"]=候选数）。绝不 fallback 成无条件
ADD——新旧事实并存正是本增强要解决的问题（旧行为直写 LTM，参数变更后
"A方案"与"B方案"同库并存导致召回互相矛盾）；宁可整轮丢弃等下一轮重判，也不
污染语义存储。

失效语义出处：Graphiti（Zep）的 episodic/entity invalidation 模型——UPDATE 不
覆盖内容，旧条目打 invalid_at/superseded_by 保留时点回溯，适配水利参数/方案
版本演变场景；DELETE 为软删除，检索默认过滤但内容可回滚。
"""

import json
import logging
from typing import Any, Dict, List, Optional

from floodmind.memory.semantic_store import SemanticMemoryStore, extract_terms

logger = logging.getLogger(__name__)

_VALID_ACTIONS = {"ADD", "UPDATE", "DELETE", "NOOP"}

_JUDGE_PROMPT = """你是记忆管理助手。对每条候选记忆，决定如何写入长期记忆存储。

## 现有记忆（每条候选的冲突候选）
{existing}

## 候选记忆
{candidates}

## 规则
- ADD：候选是全新事实，与现有记忆无冲突
- UPDATE：候选与某条现有记忆冲突或演化（参数变更、方案替换），target_id 指向被替代的旧条目
- DELETE：某条现有记忆已明确失效或被否定，target_id 指向它
- NOOP：候选是临时信息、重复内容、或无需持久化
- 没有冲突时一律 ADD，不要臆造 UPDATE

## 输出格式
严格输出 JSON 数组（不要其他文字），每个元素：
{{"index": 0, "action": "ADD|UPDATE|DELETE|NOOP", "target_id": "...", "reason": "..."}}
index 为候选序号；ADD/NOOP 不需要 target_id。数组长度必须等于候选数。"""


class MemoryConsolidationService:
    """四类操作固化服务：单轮回顾候选 → LLM 判定 → 统一应用。

    边界条件：
    - 构造时 model_client 可为 None（延迟注入，经 set_model_client），
      但未注入时调用 consolidate 会直接判败并丢弃本轮候选。
    - conflict_candidate_top_k 控制每条候选送往 LLM 的冲突候选上限。
    - consolidate 返回 {"added","updated","deleted","noop","discarded"}，
      discarded > 0 表示本轮整体丢弃（任一判定不合法即整轮作废）。
    """

    def __init__(
        self,
        store: SemanticMemoryStore,
        model_client: Any = None,
        *,
        conflict_candidate_top_k: int = 3,
    ):
        self._store = store
        self._model_client = model_client
        self._conflict_candidate_top_k = conflict_candidate_top_k

    def set_model_client(self, model_client: Any) -> None:
        """延迟注入模型客户端（与 BackgroundReviewer 同注入模式）。"""
        self._model_client = model_client

    def consolidate(self, candidates: List[Dict[str, Any]]) -> Dict[str, int]:
        """固化一轮候选。返回五个计数器；discarded 为整轮丢弃的候选数。"""
        stats = {"added": 0, "updated": 0, "deleted": 0, "noop": 0, "discarded": 0}
        # 过滤空内容候选：无实质内容时不值得消耗 LLM 调用
        candidates = [c for c in candidates if str(c.get("content", "")).strip()]
        if not candidates:
            return stats

        # 阶段一：判定 + 验证（全部通过才进入阶段二，否则整轮丢弃）
        try:
            decisions = self._judge(candidates)
            self._validate(decisions, candidates)
        except Exception as e:
            logger.warning("[记忆固化] 判定失败，本轮 %d 条候选全部丢弃: %s", len(candidates), e)
            stats["discarded"] = len(candidates)
            return stats

        # 阶段二：统一应用（UPDATE 先加新条目再失效旧条目，保证 superseded_by 可指）
        for d in decisions:
            action = d["action"]
            if action == "ADD":
                self._store.add(candidates[d["index"]]["content"],
                                category=candidates[d["index"]].get("type", "note"))
                stats["added"] += 1
            elif action == "UPDATE":
                new_entry = self._store.add(candidates[d["index"]]["content"],
                                            category=candidates[d["index"]].get("type", "note"))
                if self._store.mark_superseded(d["target_id"], new_entry.entry_id):
                    stats["updated"] += 1
            elif action == "DELETE":
                if self._store.mark_deleted(d["target_id"]):
                    stats["deleted"] += 1
            else:  # NOOP
                stats["noop"] += 1
        logger.info("[记忆固化] added=%d updated=%d deleted=%d noop=%d",
                    stats["added"], stats["updated"], stats["deleted"], stats["noop"])
        return stats

    # ── 内部 ────────────────────────────────────────────

    def _judge(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """调 LLM 批量判定候选动作。解析失败或结构非法时抛异常（由调用方丢弃本轮）。"""
        if self._model_client is None:
            raise RuntimeError("model_client 未注入")
        existing_blocks = []
        for i, c in enumerate(candidates):
            terms = extract_terms(c["content"])
            conflicts = self._store.find_candidates(terms, top_k=self._conflict_candidate_top_k)
            conflict_lines = "\n".join(
                f"  - id={e.entry_id} [{e.category}] {e.content}" for e in conflicts
            ) or "  （无）"
            existing_blocks.append(f"候选{i} 的冲突候选：\n{conflict_lines}")
        candidate_lines = "\n".join(
            f"候选{i}: [{c.get('type', 'note')}] {c['content']}" for i, c in enumerate(candidates)
        )
        prompt = _JUDGE_PROMPT.format(
            existing="\n\n".join(existing_blocks), candidates=candidate_lines
        )
        response = self._model_client.invoke(
            prompt=prompt,
            system_prompt="你是记忆管理助手，只输出规定的 JSON 数组。",
            temperature=0.2,
            max_tokens=1500,
        )
        raw = response.content if hasattr(response, "content") else str(response)
        text = raw.strip()
        # 兼容 LLM 可能包裹的 ```json 代码块
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        decisions = json.loads(text)
        if not isinstance(decisions, list):
            raise ValueError("判定结果不是数组")
        return decisions

    def _validate(self, decisions: List[Dict[str, Any]], candidates: List[Dict[str, Any]]) -> None:
        """校验判定结果。任一不合法即抛异常，触发整轮丢弃。

        实例方法（非 @staticmethod）：UPDATE/DELETE 的 target_id 存在性
        校验需要访问 store，简报代码块与注释存在结构矛盾，按"必须实例方法"裁决实现。
        """
        if len(decisions) != len(candidates):
            raise ValueError(f"判定数量 {len(decisions)} != 候选数 {len(candidates)}")
        for d in decisions:
            if not isinstance(d, dict):
                raise ValueError("判定元素不是对象")
            if d.get("action") not in _VALID_ACTIONS:
                raise ValueError(f"非法 action: {d.get('action')}")
            idx = d.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
                raise ValueError(f"非法 index: {idx}")
            # UPDATE/DELETE 必须指向真实存在的条目，否则应用阶段会静默失败或指向脏数据
            if d["action"] in ("UPDATE", "DELETE"):
                target = d.get("target_id")
                if not target or self._store.get(target) is None:
                    raise ValueError(f"target 不存在: {target}")
