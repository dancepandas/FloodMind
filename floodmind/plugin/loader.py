"""
Plugin Loader — 从配置目录自动发现和加载 FloodmindPlugin。

扫描路径（按优先级）:
    1. ~/.floodmind/plugins/*.py          — 单文件插件
    2. ~/.floodmind/plugins/*/plugin.json — 目录插件
    3. {project_root}/.floodmind/plugins/ — 项目级插件
"""

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import List

from floodmind.plugin.base import FloodmindPlugin

logger = logging.getLogger(__name__)

DEFAULT_PLUGIN_DIRS = [
    Path.home() / ".floodmind" / "plugins",
    Path(".") / ".floodmind" / "plugins",
]


class PluginLoader:
    """插件加载器：扫描指定目录，加载所有有效插件。"""

    def __init__(self, plugin_dirs: List[Path] | None = None):
        self._dirs = plugin_dirs or DEFAULT_PLUGIN_DIRS
        self._loaded: List[FloodmindPlugin] = []

    @property
    def loaded(self) -> List[FloodmindPlugin]:
        return list(self._loaded)

    def discover(self) -> List[FloodmindPlugin]:
        """扫描并加载所有插件。幂等：重复调用不会创建重复实例。"""
        for directory in self._dirs:
            if not directory.exists():
                continue

            # 1. 单文件插件: plugins/*.py (排除 __init__.py)
            for py_file in sorted(directory.glob("*.py")):
                if py_file.name.startswith("_") or py_file.name.startswith("."):
                    continue
                self._load_module(py_file)

            # 2. 目录插件: plugins/*/plugin.json
            for plugin_dir in sorted(directory.iterdir()):
                if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
                    continue
                manifest = plugin_dir / "plugin.json"
                if not manifest.exists():
                    continue
                try:
                    cfg = json.loads(manifest.read_text(encoding="utf-8"))
                    entry_module = cfg.get("entry", "plugin")
                    entry_file = plugin_dir / f"{entry_module}.py"
                    if entry_file.exists():
                        self._load_module(entry_file)
                    else:
                        # Try as Python package
                        pkg_name = f"{plugin_dir.parent.name}.{plugin_dir.name}.{entry_module}"
                        self._load_package(pkg_name)
                except Exception as e:
                    logger.warning("Failed to load plugin from %s: %s", plugin_dir, e)

        return self._loaded

    def unload_all(self) -> None:
        """卸载所有已加载的插件。"""
        for plugin in reversed(self._loaded):
            try:
                plugin.on_unload()
            except Exception as e:
                logger.warning("Plugin %s on_unload error: %s", plugin.name, e)
        self._loaded.clear()

    def _load_module(self, filepath: Path) -> None:
        """从 .py 文件加载插件。"""
        try:
            # Add parent dir to path for import
            parent = str(filepath.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)

            mod_name = filepath.stem
            mod = importlib.import_module(mod_name)

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, FloodmindPlugin)
                    and attr is not FloodmindPlugin
                ):
                    plugin = attr()
                    plugin.on_load()
                    self._loaded.append(plugin)
                    logger.info("Loaded plugin: %s v%s from %s", plugin.name, plugin.version, filepath)

        except Exception as e:
            logger.warning("Failed to load plugin module %s: %s", filepath, e)

    def _load_package(self, pkg_name: str) -> None:
        """从 Python 包加载插件。"""
        try:
            mod = importlib.import_module(pkg_name)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, FloodmindPlugin)
                    and attr is not FloodmindPlugin
                ):
                    plugin = attr()
                    plugin.on_load()
                    self._loaded.append(plugin)
                    logger.info("Loaded package plugin: %s v%s", plugin.name, plugin.version)
        except Exception as e:
            logger.warning("Failed to load plugin package %s: %s", pkg_name, e)
