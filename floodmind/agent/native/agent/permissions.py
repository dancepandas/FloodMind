"""
Agent 权限系统。

每个 Agent 角色通过权限规则控制可访问的工具。
权限规则采用"默认允许 + 显式拒绝"模式，便于配置。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class AgentPermission:
    """Agent 工具权限规则。

    规则优先级（高到低）：
    1. deny_list — 显式拒绝的工具名（最高优先级）
    2. allow_list — 显式允许的工具名
    3. default — 默认策略（allow 或 deny）

    特殊工具名 "*" 表示所有工具。
    """

    default: str = "allow"  # "allow" | "deny"
    allow_list: Set[str] = field(default_factory=set)
    deny_list: Set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentPermission":
        """从 settings.json 配置解析权限规则。"""
        if not isinstance(data, dict):
            return cls()

        tools = data.get("tools", [])
        allow_list: Set[str] = set()
        deny_list: Set[str] = set()

        if isinstance(tools, list):
            # ["*"] 或 ["read", "write"] 格式
            for t in tools:
                if t.startswith("-"):
                    deny_list.add(t[1:])
                else:
                    allow_list.add(t)
        elif isinstance(tools, dict):
            # {"read": true, "write": false} 格式
            for name, enabled in tools.items():
                if enabled:
                    allow_list.add(name)
                else:
                    deny_list.add(name)

        # 默认策略推断：
        # - 显式指定了 default → 使用显式值
        # - 未指定且 allow_list 含非 * 工具 → 默认 deny（白名单模式）
        # - 未指定且 allow_list 为空或仅含 * → 默认 allow
        if "default" in data:
            default = data["default"]
        elif allow_list and "*" not in allow_list:
            default = "deny"
        else:
            default = "allow"

        return cls(default=default, allow_list=allow_list, deny_list=deny_list)

    def can_use(self, tool_name: str) -> bool:
        """检查是否允许使用指定工具。"""
        # 1. 显式拒绝优先
        if tool_name in self.deny_list or "*" in self.deny_list:
            return False

        # 2. 显式允许
        if tool_name in self.allow_list or "*" in self.allow_list:
            return True

        # 3. 默认策略
        return self.default == "allow"

    def filter_tools(self, tools: List[Any]) -> List[Any]:
        """过滤工具列表，只保留允许的工具。"""
        result = []
        for tool in tools:
            name = tool.get("function", {}).get("name", "") if isinstance(tool, dict) else getattr(tool, "name", "")
            if self.can_use(name):
                result.append(tool)
        return result
