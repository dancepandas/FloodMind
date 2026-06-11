"""TUI 插槽系统 — 学习 OpenCode 的 Slot 插件注入机制。

核心概念：
- 预定义固定位置的插槽（如 sidebar_top, sidebar_content, sidebar_bottom）
- 插件/模块注册组件到插槽
- 按 order 排序，支持动态增删

Usage:
    slots = SlotRegistry()
    slots.register("sidebar_content", MyWidget(), order=500)
    slots.register("sidebar_content", AnotherWidget(), order=600)

    # 在 Sidebar 中渲染：
    for widget in slots.get("sidebar_content"):
        yield widget
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from textual.widget import Widget


class SlotEntry:
    """插槽条目"""

    def __init__(self, widget: Widget, order: int = 500, plugin_id: str = ""):
        self.widget = widget
        self.order = order
        self.plugin_id = plugin_id


class SlotRegistry:
    """插槽注册表 — 管理所有可扩展的 UI 插槽。"""

    # 预定义插槽名称（固定位置，不随意变动）
    SIDEBAR_TOP = "sidebar_top"
    SIDEBAR_CONTENT = "sidebar_content"
    SIDEBAR_BOTTOM = "sidebar_bottom"
    CHAT_HEADER = "chat_header"
    CHAT_FOOTER = "chat_footer"
    DETAIL_HEADER = "detail_header"
    DETAIL_CONTENT = "detail_content"
    STATUS_BAR = "status_bar"

    _BUILTIN_SLOTS = [
        SIDEBAR_TOP,
        SIDEBAR_CONTENT,
        SIDEBAR_BOTTOM,
        CHAT_HEADER,
        CHAT_FOOTER,
        DETAIL_HEADER,
        DETAIL_CONTENT,
        STATUS_BAR,
    ]

    def __init__(self):
        self._slots: Dict[str, List[SlotEntry]] = {
            name: [] for name in self._BUILTIN_SLOTS
        }

    def register(
        self,
        slot_name: str,
        widget: Widget,
        order: int = 500,
        plugin_id: str = "",
    ) -> None:
        """注册组件到指定插槽。

        Args:
            slot_name: 插槽名称
            widget: 要注入的组件
            order: 排序权重（越小越靠前）
            plugin_id: 插件标识（用于后续注销）
        """
        if slot_name not in self._slots:
            self._slots[slot_name] = []

        entry = SlotEntry(widget, order, plugin_id)
        self._slots[slot_name].append(entry)
        # 按 order 排序
        self._slots[slot_name].sort(key=lambda e: e.order)

    def unregister(self, slot_name: str, plugin_id: str = "") -> None:
        """注销指定插件的组件。"""
        if slot_name not in self._slots:
            return
        if plugin_id:
            self._slots[slot_name] = [
                e for e in self._slots[slot_name] if e.plugin_id != plugin_id
            ]
        else:
            self._slots[slot_name] = []

    def get(self, slot_name: str) -> List[Widget]:
        """获取插槽中的所有组件（已按 order 排序）。"""
        entries = self._slots.get(slot_name, [])
        return [e.widget for e in entries]

    def has(self, slot_name: str) -> bool:
        """插槽是否有内容。"""
        return len(self._slots.get(slot_name, [])) > 0

    def clear(self) -> None:
        """清空所有插槽。"""
        for name in self._slots:
            self._slots[name] = []

    def list_slots(self) -> List[str]:
        """列出所有有内容的插槽名称。"""
        return [name for name, entries in self._slots.items() if entries]
