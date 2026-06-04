"""输入历史管理，持久化到 ~/.config/floodmind/history.json"""

import json
from pathlib import Path
from typing import List


def _history_path() -> Path:
    dir = Path.home() / ".config" / "floodmind"
    dir.mkdir(parents=True, exist_ok=True)
    return dir / "history.json"


def load_history() -> List[str]:
    p = _history_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_entry(text: str) -> None:
    history = load_history()
    # 去重：如果与最近一条相同则跳过
    if history and history[-1] == text:
        return
    history.append(text)
    # 最多保留 500 条
    if len(history) > 500:
        history = history[-500:]
    with open(_history_path(), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
