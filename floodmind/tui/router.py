"""TUI 路由系统 — 学习 OpenCode 的路由驱动导航。

核心概念：
- 路由表注册屏幕（screen）和模态框（modal）
- 导航时保存状态到路由参数
- 支持返回栈和深度链接
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from textual.screen import Screen


@dataclass
class Route:
    """路由定义"""
    name: str
    screen_factory: Callable[..., "Screen"]
    is_modal: bool = False
    default_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteState:
    """路由状态（可序列化）"""
    name: str
    params: Dict[str, Any] = field(default_factory=dict)


class Router:
    """TUI 路由器。

    Usage:
        router = Router(app)
        router.register("home", HomeScreen)
        router.register("chat/:session_id", MainScreen)
        router.navigate("chat/session-123")  # 带参数导航
        router.back()  # 返回上一页
    """

    def __init__(self, app):
        self._app = app
        self._routes: Dict[str, Route] = {}
        self._back_stack: List[RouteState] = []
        self._current: Optional[RouteState] = None

    def register(
        self,
        path: str,
        screen_factory: Callable[..., "Screen"],
        is_modal: bool = False,
        **default_params,
    ) -> None:
        """注册路由。

        Args:
            path: 路由路径，如 "home" 或 "chat/:session_id"
            screen_factory: Screen 类或工厂函数
            is_modal: 是否为模态框
        """
        self._routes[path] = Route(
            name=path,
            screen_factory=screen_factory,
            is_modal=is_modal,
            default_params=default_params,
        )

    def navigate(self, path: str, **params) -> None:
        """导航到指定路由。

        Args:
            path: 路由路径（可带参数，如 "chat/session-123"）
            **params: 额外参数
        """
        # 解析路径和参数
        route_name, path_params = self._parse_path(path)
        merged_params = {**path_params, **params}

        route = self._routes.get(route_name)
        if not route:
            raise ValueError(f"未注册的路由: {route_name}")

        # 保存当前状态到返回栈
        if self._current:
            self._back_stack.append(self._current)

        self._current = RouteState(name=route_name, params=merged_params)

        # 创建并推送屏幕
        screen = route.screen_factory(**merged_params)
        if route.is_modal:
            self._app.push_screen(screen)
        else:
            self._app.push_screen(screen)

    def back(self) -> bool:
        """返回上一页。返回是否成功。"""
        if not self._back_stack:
            return False

        prev = self._back_stack.pop()
        self._current = prev

        route = self._routes.get(prev.name)
        if route:
            screen = route.screen_factory(**prev.params)
            self._app.pop_screen()
            self._app.push_screen(screen)
        return True

    def replace(self, path: str, **params) -> None:
        """替换当前路由（不压入返回栈）。"""
        route_name, path_params = self._parse_path(path)
        merged_params = {**path_params, **params}

        route = self._routes.get(route_name)
        if not route:
            raise ValueError(f"未注册的路由: {route_name}")

        self._current = RouteState(name=route_name, params=merged_params)
        screen = route.screen_factory(**merged_params)
        self._app.switch_screen(screen)

    def _parse_path(self, path: str) -> tuple:
        """解析路径，返回 (route_name, params)。"""
        # 简单实现：path 如 "chat/session-123" → ("chat/:session_id", {"session_id": "session-123"})
        # 先尝试精确匹配
        if path in self._routes:
            return path, {}

        # 尝试模式匹配
        parts = path.split("/")
        for route_name in self._routes:
            route_parts = route_name.split("/")
            if len(parts) != len(route_parts):
                continue

            params = {}
            matched = True
            for rp, p in zip(route_parts, parts):
                if rp.startswith(":"):
                    params[rp[1:]] = p
                elif rp != p:
                    matched = False
                    break

            if matched:
                return route_name, params

        #  fallback：返回路径本身作为名称
        return path, {}

    @property
    def current(self) -> Optional[RouteState]:
        return self._current

    @property
    def can_go_back(self) -> bool:
        return len(self._back_stack) > 0
