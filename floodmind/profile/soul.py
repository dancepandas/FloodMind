import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_FLOODMIND_IDENTITY = (
    "你是 FloodMind，一个开源的智能水文预报 Agent 系统。\n"
    "你帮助用户完成水文预报、数据分析、文档生成等各类任务。\n"
    "你高效、专业、直接，优先自己完成任务，合理使用子代理提高效率。"
)

DEFAULT_SOUL_MD = """你是 FloodMind，一个开源的智能水文预报 Agent 系统。

## 角色职责
1. 分析用户意图和最终目标
2. 规划任务步骤（复杂任务建议先 create_plan）
3. 处理无顺序依赖时启动子代理
4. 汇总结果并回答用户

## 核心特质
- 高效专业，擅长水文预报与数据分析
- 直接明了，不废话不绕弯
- 主动思考，善于利用工具完成任务
- 优先自己完成需要丰富上下文的任务
"""

SOUL_MD_MAX_CHARS = 20_000


def get_floodmind_home_path() -> Path:
    from floodmind.config.settings import get_floodmind_home
    return get_floodmind_home()


def load_soul_md() -> Optional[str]:
    try:
        soul_path = get_floodmind_home_path() / "SOUL.md"
        if not soul_path.exists():
            return None
        content = soul_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        if len(content) > SOUL_MD_MAX_CHARS:
            logger.warning("SOUL.md 超过 %d 字符，已截断", SOUL_MD_MAX_CHARS)
            content = content[:SOUL_MD_MAX_CHARS]
        return content
    except Exception as e:
        logger.debug("无法读取 SOUL.md: %s", e)
        return None


def seed_default_soul() -> None:
    try:
        home = get_floodmind_home_path()
        home.mkdir(parents=True, exist_ok=True)
        soul_path = home / "SOUL.md"
        if not soul_path.exists():
            soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
            logger.info("已创建默认 SOUL.md: %s", soul_path)
    except Exception as e:
        logger.debug("无法创建默认 SOUL.md: %s", e)
