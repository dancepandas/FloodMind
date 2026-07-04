"""
Skill Curator — 技能生命周期管理

职责：
  - 追踪 skill 使用频率和成功率
  - 自动检测并归档长期未用的 skill
  - 检测重复/相似 skill
  - 管理 skill 状态（active/stale/archived）

设计原则：
  - 只管理用户创建的技能（skills/ 目录），不动内置技能
  - 从不自动删除，只归档（可恢复）
  - 提供手动触发和配置项控制
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from floodmind.skills.base import Skill, discover_skills
from floodmind.skills.registry import get_skill_registry

logger = logging.getLogger(__name__)

DEFAULT_STALE_DAYS = 30
DEFAULT_ARCHIVE_DAYS = 90


@dataclass
class SkillUsageRecord:
    """单条使用记录"""

    skill_name: str
    timestamp: str
    success: bool = True
    session_id: str = ""


@dataclass
class SkillStat:
    """技能统计"""

    skill_name: str
    total_uses: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str = ""
    first_used_at: str = ""
    status: str = "active"  # active | stale | archived
    archived_at: str = ""

    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def days_since_last_use(self) -> int:
        if not self.last_used_at:
            return 9999
        try:
            last = datetime.fromisoformat(self.last_used_at)
            return (datetime.now() - last).days
        except Exception:
            return 9999


class SkillCurator:
    """
    技能策展人。

    使用方式：
        curator = SkillCurator()
        curator.record_usage("aojiang-hydro", success=True)
        stats = curator.get_stats()
        stale = curator.find_stale_skills()
        curator.run_maintenance()
    """

    def __init__(
        self,
        skills_dirs: Optional[List[str]] = None,
        state_file: str = ".floodmind/skill_curator.json",
        archive_root: Optional[Path] = None,
        stale_days: int = DEFAULT_STALE_DAYS,
        archive_days: int = DEFAULT_ARCHIVE_DAYS,
    ):
        # 统一 SkillRegistry 为 skill 目录的唯一发现源（不再自己扫 .floodmind/skills）
        _custom_dirs = skills_dirs is not None
        if skills_dirs is None:
            skills_dirs = [str(d) for d in get_skill_registry().roots]
        self.skills_dirs = [Path(d) for d in skills_dirs]
        self.state_file = Path(state_file)
        # 主要写入/恢复根：自定义目录用第一个，否则用 registry.writable_root
        if _custom_dirs:
            self._primary_root = self.skills_dirs[0]
        else:
            self._primary_root = get_skill_registry().writable_root
        # 归档根：默认 _primary_root/.archived（与 RemoveSkill 一致）
        if archive_root is None:
            archive_root = self._primary_root / ".archived"
        self.archive_root = Path(archive_root)
        self.stale_days = stale_days
        self.archive_days = archive_days
        self._stats: Dict[str, SkillStat] = {}
        self._lock = threading.RLock()
        self._load()

    # ── State persistence ───────────────────────────────────────

    def _load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text("utf-8"))
            for name, stat_data in data.get("stats", {}).items():
                self._stats[name] = SkillStat(**stat_data)
        except Exception as e:
            logger.warning("Skill curator state load failed: %s", e)

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "updated_at": datetime.now().isoformat(),
                "stats": {name: asdict(stat) for name, stat in self._stats.items()},
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(self.state_file))
        except Exception as e:
            logger.warning("Skill curator state save failed: %s", e)

    # ── Usage tracking ──────────────────────────────────────────

    def record_usage(self, skill_name: str, success: bool = True, session_id: str = "") -> None:
        """记录一次 skill 使用"""
        with self._lock:
            now = datetime.now().isoformat()
            stat = self._stats.get(skill_name)
            if stat is None:
                stat = SkillStat(
                    skill_name=skill_name,
                    first_used_at=now,
                    status="active",
                )
                self._stats[skill_name] = stat

            stat.total_uses += 1
            if success:
                stat.success_count += 1
            else:
                stat.failure_count += 1
            stat.last_used_at = now

            # 如果之前被标记为 stale/archived，重新激活
            if stat.status in ("stale", "archived"):
                stat.status = "active"
                stat.archived_at = ""
                logger.info("Skill %s reactivated by usage", skill_name)

            self._save()

    def get_stats(self) -> List[Dict[str, Any]]:
        """获取所有 skill 统计"""
        with self._lock:
            result = []
            for stat in self._stats.values():
                result.append(
                    {
                        "skill_name": stat.skill_name,
                        "total_uses": stat.total_uses,
                        "success_rate": round(stat.success_rate(), 2),
                        "last_used_at": stat.last_used_at,
                        "first_used_at": stat.first_used_at,
                        "status": stat.status,
                        "days_since_last_use": stat.days_since_last_use(),
                    }
                )
            return sorted(result, key=lambda x: x["total_uses"], reverse=True)

    def get_skill_stat(self, skill_name: str) -> Optional[SkillStat]:
        return self._stats.get(skill_name)

    # ── Stale detection ─────────────────────────────────────────

    def find_stale_skills(self) -> List[SkillStat]:
        """查找超过 stale_days 未使用的 active skill"""
        with self._lock:
            result = []
            for stat in self._stats.values():
                if stat.status != "active":
                    continue
                days = stat.days_since_last_use()
                if days >= self.stale_days:
                    result.append(stat)
            return sorted(result, key=lambda s: s.days_since_last_use(), reverse=True)

    def find_archive_candidates(self) -> List[SkillStat]:
        """查找超过 archive_days 未使用的 stale skill"""
        with self._lock:
            result = []
            for stat in self._stats.values():
                if stat.status != "stale":
                    continue
                days = stat.days_since_last_use()
                if days >= self.archive_days:
                    result.append(stat)
            return result

    # ── Duplicate detection ─────────────────────────────────────

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """简单文本相似度：Jaccard 基于字符二元组"""
        def _bigrams(text):
            text = text.lower()
            return set(text[i : i + 2] for i in range(len(text) - 1))

        bg_a = _bigrams(a)
        bg_b = _bigrams(b)
        if not bg_a or not bg_b:
            return 0.0
        inter = len(bg_a & bg_b)
        union = len(bg_a | bg_b)
        return inter / union if union > 0 else 0.0

    def _find_skill_dir(self, skill_name: str) -> Optional[Path]:
        """查找 skill 所在的根目录（搜索 skills_dirs + _primary_root 回退）。"""
        for d in self.skills_dirs:
            candidate = d / skill_name
            if candidate.exists() and (candidate / "SKILL.md").exists():
                return d
        # 回退：检查 _primary_root（可能与 skills_dirs 不重叠）
        candidate = self._primary_root / skill_name
        if candidate.exists() and (candidate / "SKILL.md").exists():
            return self._primary_root
        return None

    def find_duplicates(self, threshold: float = 0.7) -> List[Tuple[str, str, float]]:
        """
        查找描述相似的 skill 对（扫描 curator 的 skills_dirs，默认即 SkillRegistry roots）。
        返回 [(skill_a, skill_b, similarity), ...]
        """
        all_skills = []
        for d in self.skills_dirs:
            if d.exists():
                all_skills.extend(discover_skills(d))

        if len(all_skills) < 2:
            return []

        seen = set()
        duplicates = []
        for i in range(len(all_skills)):
            for j in range(i + 1, len(all_skills)):
                a, b = all_skills[i], all_skills[j]
                pair_key = tuple(sorted([a.name, b.name]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                text_a = f"{a.name} {a.description} {a.prompt[:500]}"
                text_b = f"{b.name} {b.description} {b.prompt[:500]}"
                sim = self._text_similarity(text_a, text_b)
                if sim >= threshold:
                    duplicates.append((a.name, b.name, round(sim, 3)))

        return sorted(duplicates, key=lambda x: x[2], reverse=True)

    # ── Archive / Restore ───────────────────────────────────────

    def archive_skill(self, skill_name: str) -> bool:
        """
        归档 skill：移动到 archive_root（默认 writable_root/.archived/）。
        返回是否成功。
        """
        base_dir = self._find_skill_dir(skill_name)
        if base_dir is None:
            logger.warning("Skill %s not found, cannot archive", skill_name)
            return False

        skill_dir = base_dir / skill_name
        archive_dir = self.archive_root / skill_name
        try:
            archive_dir.parent.mkdir(parents=True, exist_ok=True)
            if archive_dir.exists():
                shutil.rmtree(archive_dir)
            shutil.move(str(skill_dir), str(archive_dir))

            with self._lock:
                stat = self._stats.get(skill_name)
                if stat:
                    stat.status = "archived"
                    stat.archived_at = datetime.now().isoformat()
                else:
                    # 即使没有使用记录也创建一条
                    self._stats[skill_name] = SkillStat(
                        skill_name=skill_name,
                        status="archived",
                        archived_at=datetime.now().isoformat(),
                    )
                self._save()

            logger.info("Skill archived: %s -> %s", skill_name, archive_dir)
            return True
        except Exception as e:
            logger.error("Failed to archive skill %s: %s", skill_name, e)
            return False

    def _find_archive_dir(self, skill_name: str) -> Optional[Path]:
        """在 archive_root 中查找归档目录。"""
        archive_dir = self.archive_root / skill_name
        if archive_dir.exists():
            return archive_dir
        return None

    def restore_skill(self, skill_name: str) -> bool:
        """从归档恢复 skill（恢复到 _primary_root）。"""
        archive_dir = self._find_archive_dir(skill_name)
        if archive_dir is None:
            logger.warning("Archived skill %s not found", skill_name)
            return False

        skill_dir = self._primary_root / skill_name
        try:
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            shutil.move(str(archive_dir), str(skill_dir))

            with self._lock:
                stat = self._stats.get(skill_name)
                if stat:
                    stat.status = "active"
                    stat.archived_at = ""
                self._save()

            logger.info("Skill restored: %s", skill_name)
            return True
        except Exception as e:
            logger.error("Failed to restore skill %s: %s", skill_name, e)
            return False

    def list_archived(self) -> List[str]:
        """列出所有已归档的 skill 名称（archive_root 下）。"""
        archived = set()
        if self.archive_root.exists():
            for item in self.archive_root.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    archived.add(item.name)
        return sorted(archived)

    # ── Maintenance ─────────────────────────────────────────────

    def run_maintenance(self) -> Dict[str, Any]:
        """
        运行维护：标记 stale、归档过期 skill。
        返回维护报告。
        """
        with self._lock:
            report = {
                "stale_marked": 0,
                "archived": 0,
                "duplicates_found": 0,
                "timestamp": datetime.now().isoformat(),
            }

            # 1. 标记 stale
            for stat in self._stats.values():
                if stat.status != "active":
                    continue
                if stat.days_since_last_use() >= self.stale_days:
                    stat.status = "stale"
                    report["stale_marked"] += 1
                    logger.info("Skill marked stale: %s", stat.skill_name)

            # 2. 归档过期的 stale
            for stat in list(self._stats.values()):
                if stat.status != "stale":
                    continue
                if stat.days_since_last_use() >= self.archive_days:
                    if self.archive_skill(stat.skill_name):
                        report["archived"] += 1

            # 3. 检测重复
            duplicates = self.find_duplicates()
            report["duplicates_found"] = len(duplicates)
            report["duplicates"] = duplicates[:5]  # 只报告前5对

            self._save()
            logger.info(
                "Skill curator maintenance: stale=%d archived=%d duplicates=%d",
                report["stale_marked"],
                report["archived"],
                report["duplicates_found"],
            )
            return report


# ── Global instance ───────────────────────────────────────────

_curator: Optional[SkillCurator] = None
_curator_lock = threading.Lock()


def get_skill_curator() -> SkillCurator:
    global _curator
    if _curator is None:
        with _curator_lock:
            if _curator is None:
                _curator = SkillCurator()
    return _curator


def record_skill_usage(skill_name: str, success: bool = True, session_id: str = "") -> None:
    """便捷函数：记录 skill 使用"""
    try:
        get_skill_curator().record_usage(skill_name, success, session_id)
    except Exception as e:
        logger.debug("Record skill usage failed: %s", e)


# ── Maintenance scheduler ─────────────────────────────────────

_MAINTENANCE_INTERVAL_HOURS = 6  # 对标 task_experience 默认间隔


def run_maintenance_if_needed(state_dir: Optional[Path] = None, force: bool = False) -> Optional[Dict[str, Any]]:
    """按时间间隔运行 curator 巡检（标记文件防重入，对标 TaskExperienceStore 模式）。

    在 NativeFloodAgent._init_tools 中每次启动时调用；标记文件控制频率，避免频繁巡检。
    """
    if state_dir is None:
        try:
            state_dir = get_skill_registry().writable_root
        except Exception:
            return None

    marker_file = state_dir / ".last_skill_maintenance"
    if not force and marker_file.exists():
        try:
            last = datetime.fromisoformat(marker_file.read_text(encoding="utf-8").strip())
            if datetime.now() - last < timedelta(hours=_MAINTENANCE_INTERVAL_HOURS):
                return None
        except Exception:
            pass  # 标记文件损坏则执行

    try:
        curator = get_skill_curator()
        report = curator.run_maintenance()
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        marker_file.write_text(datetime.now().isoformat(), encoding="utf-8")
        logger.info("Skill curator 巡检完成: %s", report)
        return report
    except Exception as e:
        logger.warning("Skill curator 巡检失败: %s", e)
        return None
