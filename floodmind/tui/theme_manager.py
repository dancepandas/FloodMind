"""TUI 主题管理器 — 学习 OpenCode 的 JSON 主题系统。

核心概念：
- 主题定义保存为 JSON 文件（如 smoke-theme.json）
- 语义化颜色 token（primary, border, textMuted 等）
- 运行时切换主题
- 尊重 NO_COLOR 环境变量

Usage:
    theme = ThemeManager()
    theme.load("default")  # 加载 themes/default.json
    color = theme.get("primary")  # 获取语义颜色
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# 默认主题（Dark，类似 OpenCode 的 smoke-theme）
DEFAULT_THEME = {
    "name": "default",
    "description": "FloodMind 默认暗色主题",
    "colors": {
        "background": "#0a0a0f",
        "backgroundPanel": "#14141f",
        "surface": "#1a1a2e",
        "border": "#2d2d3d",
        "borderFocus": "#3a5a8c",
        "text": "#e0e0e0",
        "textMuted": "#808090",
        "textDim": "#505060",
        "primary": "#5f87ff",
        "primaryMuted": "#4a6fd4",
        "success": "#4caf7d",
        "warning": "#e5a443",
        "error": "#e54d4d",
        "info": "#5fb4ff",
        "accent": "#7c6fae",
        "selected": "#3a5a8c",
        "highlight": "#5f87ff",
    },
    "styles": {
        "borderType": "single",  # single / rounded / heavy / ascii
        "borderFocusStyle": "bold",
        "scrollbar": "#2d2d3d",
        "cursor": "#5f87ff",
    },
}


class ThemeManager:
    """主题管理器 — 支持 JSON 主题文件和运行时切换。"""

    def __init__(self, themes_dir: Optional[Path] = None):
        self._themes_dir = themes_dir or (Path.home() / ".floodmind" / "themes")
        self._themes_dir.mkdir(parents=True, exist_ok=True)
        self._current_name = "default"
        self._current_theme: Dict[str, Any] = dict(DEFAULT_THEME)
        self._no_color = os.environ.get("NO_COLOR", "").strip() != ""
        self._themes: Dict[str, Dict[str, Any]] = {}

        # 加载用户主题目录下的主题
        self._load_all_themes()

    def _load_all_themes(self) -> None:
        """加载 themes_dir 下的所有 .json 主题文件。"""
        if not self._themes_dir.exists():
            return
        for path in self._themes_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                name = data.get("name", path.stem)
                self._themes[name] = data
            except Exception as e:
                logger.warning(f"加载主题文件失败 {path}: {e}")

    def load(self, name: str) -> bool:
        """加载指定主题。返回是否成功。"""
        if name == "default":
            self._current_theme = dict(DEFAULT_THEME)
            self._current_name = name
            return True

        # 从已加载的主题中查找
        if name in self._themes:
            self._current_theme = dict(self._themes[name])
            self._current_name = name
            return True

        # 从文件加载
        theme_file = self._themes_dir / f"{name}.json"
        if theme_file.exists():
            try:
                data = json.loads(theme_file.read_text(encoding="utf-8"))
                self._current_theme = data
                self._current_name = name
                self._themes[name] = data
                logger.info(f"主题已加载: {name}")
                return True
            except Exception as e:
                logger.error(f"加载主题失败 {name}: {e}")

        logger.warning(f"主题不存在: {name}")
        return False

    def get(self, token: str, fallback: str = "") -> str:
        """获取语义颜色值。

        Args:
            token: 颜色 token，如 "primary", "textMuted"
            fallback: 回退值
        """
        if self._no_color:
            return ""
        colors = self._current_theme.get("colors", {})
        return colors.get(token, fallback)

    def get_style(self, token: str, fallback: str = "") -> str:
        """获取样式值。"""
        styles = self._current_theme.get("styles", {})
        return styles.get(token, fallback)

    def color(self, token: str, fallback: str = "") -> str:
        """同 get()，语义化别名。"""
        return self.get(token, fallback)

    def get_all_colors(self) -> Dict[str, str]:
        """获取当前主题的所有颜色定义。"""
        return dict(self._current_theme.get("colors", {}))

    def list_themes(self) -> list:
        """列出所有可用主题。"""
        # 确保 default 存在
        themes = ["default"]
        # 从目录扫描
        if self._themes_dir.exists():
            for path in self._themes_dir.glob("*.json"):
                name = path.stem
                if name not in themes:
                    themes.append(name)
        return themes

    @property
    def current_name(self) -> str:
        return self._current_name

    @property
    def no_color(self) -> bool:
        return self._no_color

    def save_current(self, name: str) -> None:
        """将当前主题保存为 JSON 文件。"""
        theme_file = self._themes_dir / f"{name}.json"
        try:
            theme_file.write_text(
                json.dumps(self._current_theme, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"主题已保存: {theme_file}")
        except Exception as e:
            logger.error(f"保存主题失败: {e}")

    @classmethod
    def get_default_theme(cls) -> Dict[str, Any]:
        """获取默认主题定义（可用于创建新主题）。"""
        return dict(DEFAULT_THEME)
