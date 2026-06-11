"""
Web Search 配置管理 — 单文件模式

配置路径：
  - 全局: ~/.floodmind/search.json

格式：
  {
    "engine": "baidu_qianfan",
    "url": "https://qianfan.baidubce.com/v2/ai_search/web_search",
    "api_key": "your_key_here"
  }

engine 可选值：
  - baidu_qianfan（默认）
  - 任意自定义值（配合自定义 url 使用）
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GLOBAL_SEARCH_PATH = Path.home() / ".floodmind" / "search.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "engine": "baidu_qianfan",
    "url": "https://qianfan.baidubce.com/v2/ai_search/web_search",
    "api_key": "",
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("加载 search config 失败 %s: %s", path, e)
        return {}


def load_search_config() -> Dict[str, Any]:
    """加载搜索配置：文件配置 + 环境变量覆盖。"""
    cfg = dict(DEFAULT_CONFIG)
    file_cfg = _load_json(_GLOBAL_SEARCH_PATH)
    if file_cfg:
        cfg.update(file_cfg)

    # 环境变量覆盖
    env_engine = os.getenv("FLOODMIND_SEARCH_ENGINE")
    if env_engine:
        cfg["engine"] = env_engine
    env_url = os.getenv("FLOODMIND_SEARCH_URL")
    if env_url:
        cfg["url"] = env_url
    env_key = os.getenv("BAIDU_API_KEY") or os.getenv("FLOODMIND_SEARCH_API_KEY")
    if env_key:
        cfg["api_key"] = env_key

    return cfg


def get_search_config() -> Dict[str, Any]:
    """获取搜索配置（带缓存）"""
    # 简单单请求缓存，避免同一次调用中重复读文件
    if not hasattr(get_search_config, "_cache"):
        get_search_config._cache = load_search_config()
    return get_search_config._cache


def invalidate_search_config() -> None:
    """清除搜索配置缓存"""
    if hasattr(get_search_config, "_cache"):
        delattr(get_search_config, "_cache")


def write_search_config(cfg: Dict[str, Any]) -> Path:
    """写入搜索配置到全局文件"""
    _GLOBAL_SEARCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_GLOBAL_SEARCH_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    invalidate_search_config()
    logger.info("Search config written: %s", _GLOBAL_SEARCH_PATH)
    return _GLOBAL_SEARCH_PATH
