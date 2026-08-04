"""渐进式工具参数加载与工具目录检索。

工具注册表仍是唯一事实源；本模块只负责从 registry 构建轻量目录、
按需返回完整工具说明，并在 progressive 模式下维护本 executor 的 loaded set。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from floodmind.agent.runtime.contracts.tools import ToolSpec


TOOL_LOADING_MODES = {"eager", "catalog", "progressive"}
DEFAULT_CORE_TOOLS = ["SearchTools", "GetTool", "GetSkill"]


@dataclass
class ToolLoadingConfig:
    mode: str = "progressive"
    core_tools: List[str] = field(default_factory=lambda: list(DEFAULT_CORE_TOOLS))
    max_search_results: int = 8
    max_loaded_tools: int = 12
    get_tool_loads_tool: bool = True

    @classmethod
    def from_settings(cls, settings_obj: Any) -> "ToolLoadingConfig":
        raw = getattr(settings_obj, "tool_loading", None)
        mode = str(getattr(raw, "mode", "progressive") or "progressive").lower()
        if mode not in TOOL_LOADING_MODES:
            mode = "progressive"
        core_tools = list(getattr(raw, "core_tools", DEFAULT_CORE_TOOLS) or DEFAULT_CORE_TOOLS)
        return cls(
            mode=mode,
            core_tools=core_tools,
            max_search_results=int(getattr(raw, "max_search_results", 8) or 8),
            max_loaded_tools=int(getattr(raw, "max_loaded_tools", 12) or 12),
            get_tool_loads_tool=bool(getattr(raw, "get_tool_loads_tool", True)),
        )


def resolve_tool_loading_config(value: Any = None, settings_obj: Any = None) -> ToolLoadingConfig:
    """Normalize SDK/runtime tool-loading input to a ``ToolLoadingConfig``.

    Semantics:
    - ``None``: use settings defaults when available, otherwise package defaults.
    - ``False``: eager mode, preserving legacy "all schemas every request" behavior.
    - ``True``: progressive mode with package defaults.
    - ``ToolLoadingConfig``: use the provided config as-is.
    """
    if isinstance(value, ToolLoadingConfig):
        return value
    if value is False:
        return ToolLoadingConfig(mode="eager")
    if value is True:
        return ToolLoadingConfig(mode="progressive")
    if value is None:
        if settings_obj is not None:
            return ToolLoadingConfig.from_settings(settings_obj)
        return ToolLoadingConfig()
    raise TypeError(
        "tool_loading must be None, bool, or floodmind.ToolLoadingConfig; "
        f"got {type(value).__name__}"
    )


@dataclass
class ToolCatalogEntry:
    name: str
    description: str
    short_description: str
    parameter_names: List[str]
    required_parameters: List[str]
    is_readonly: bool = True
    is_destructive: bool = False
    is_concurrency_safe: bool = True

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> "ToolCatalogEntry":
        params = spec.parameters or {}
        properties = params.get("properties") if isinstance(params, dict) else {}
        if not isinstance(properties, dict):
            properties = {}
        required = params.get("required") if isinstance(params, dict) else []
        if not isinstance(required, list):
            required = []
        desc = spec.description or ""
        return cls(
            name=spec.name,
            description=desc,
            short_description=short_description(desc),
            parameter_names=list(properties.keys()),
            required_parameters=[str(x) for x in required],
            is_readonly=bool(getattr(spec, "is_readonly", True)),
            is_destructive=bool(getattr(spec, "is_destructive", False)),
            is_concurrency_safe=bool(getattr(spec, "is_concurrency_safe", True)),
        )


def short_description(description: str, limit: int = 80) -> str:
    desc = (description or "").strip().replace("\n", " ")
    if not desc:
        return ""
    first = desc.split("。")[0].split(".")[0].strip()
    return first[:limit]


def _registry_tools(registry: Any) -> List[ToolSpec]:
    if registry is None or not hasattr(registry, "all"):
        return []
    try:
        return list(registry.all())
    except Exception:
        return []


def _registry_get(registry: Any, name: str) -> Optional[ToolSpec]:
    if registry is None or not hasattr(registry, "get"):
        return None
    try:
        return registry.get(name)
    except Exception:
        return None


def build_catalog(registry: Any) -> List[ToolCatalogEntry]:
    return [ToolCatalogEntry.from_spec(spec) for spec in _registry_tools(registry)]


def compact_prompt_catalog(registry: Any, *, mode: str = "eager") -> str:
    entries = build_catalog(registry)
    if not entries:
        return "- (无工具注册)"

    if mode in ("eager", "catalog"):
        return "工具已通过请求的 tools schema 提供；本段不重复列工具目录或参数。"

    lines = [
        "progressive 模式：本段只列未完整加载工具的名称和简短说明。",
        "未加载工具不能直接调用；需要某项能力时先调用 `SearchTools`，再调用 `GetTool` 查看参数并加载工具。",
    ]

    for entry in entries:
        flags = []
        if entry.is_destructive:
            flags.append("destructive")
        elif entry.is_readonly:
            flags.append("readonly")
        flag_text = f" [{' / '.join(flags)}]" if flags else ""
        if entry.short_description:
            lines.append(f"- `{entry.name}`{flag_text}：{entry.short_description}")
        else:
            lines.append(f"- `{entry.name}`{flag_text}")
    return "\n".join(lines)


class ToolLoader:
    """单个 executor/agent 实例的工具加载状态。"""

    def __init__(self, config: Optional[ToolLoadingConfig] = None):
        self.config = config or ToolLoadingConfig()
        self.loaded_tools: Set[str] = set(self.config.core_tools)
        self.last_search_results: List[str] = []

    @property
    def mode(self) -> str:
        return self.config.mode

    def clone(self) -> "ToolLoader":
        return ToolLoader(config=ToolLoadingConfig(
            mode=self.config.mode,
            core_tools=list(self.config.core_tools),
            max_search_results=self.config.max_search_results,
            max_loaded_tools=self.config.max_loaded_tools,
            get_tool_loads_tool=self.config.get_tool_loads_tool,
        ))

    def request_tools(self, registry: Any, fallback_schema: Optional[List[dict]] = None) -> Optional[List[dict]]:
        if self.mode in ("eager", "catalog"):
            if fallback_schema is not None:
                return fallback_schema or None
            tools = [spec.to_openai_tool() for spec in _registry_tools(registry)]
            return tools or None

        names = self._effective_loaded_names(registry)
        tools = []
        for name in names:
            spec = _registry_get(registry, name)
            if spec is not None:
                tools.append(spec.to_openai_tool())
        return tools or None

    def is_executable(self, name: str) -> bool:
        if self.mode != "progressive":
            return True
        return name in self.loaded_tools

    def mark_loaded(self, name: str) -> bool:
        if not name:
            return False
        if name in self.loaded_tools:
            return True
        non_core = [n for n in self.loaded_tools if n not in self.config.core_tools]
        if len(non_core) >= self.config.max_loaded_tools:
            return False
        self.loaded_tools.add(name)
        return True

    def search(self, registry: Any, query: str, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        q = str(query or "").strip().lower()
        limit = max(1, min(int(max_results or self.config.max_search_results), self.config.max_search_results))
        scored = []
        for entry in build_catalog(registry):
            score = 0
            reasons = []
            lname = entry.name.lower()
            ldesc = entry.description.lower()
            params = [p.lower() for p in entry.parameter_names]
            if q and q in lname:
                score += 100
                reasons.append("name")
            if q and q in ldesc:
                score += 40
                reasons.append("description")
            if q and any(q in p for p in params):
                score += 30
                reasons.append("parameter")
            tokens = [t for t in q.replace("_", " ").replace(":", " ").split() if t]
            # 中文查询常没有空格；补充 2 字滑窗，避免 “天气查询” 无法命中
            # “查询...天气” 这类描述。
            if q and not tokens:
                tokens = [q]
            if q and all(ord(ch) > 127 for ch in q):
                tokens.extend(q[i:i + 2] for i in range(max(len(q) - 1, 0)))
            for token in tokens:
                if token in lname:
                    score += 20
                if token in ldesc:
                    score += 8
                if any(token in p for p in params):
                    score += 6
            if not q:
                score = 1
            if score > 0:
                scored.append((score, entry, reasons or ["keyword"]))

        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        results = []
        for _, entry, reasons in scored[:limit]:
            results.append({
                "name": entry.name,
                "description": entry.short_description,
                "required_parameters": entry.required_parameters,
                "readonly": entry.is_readonly,
                "destructive": entry.is_destructive,
                "loaded": entry.name in self.loaded_tools,
                "match": ",".join(sorted(set(reasons))),
            })
        self.last_search_results = [r["name"] for r in results]
        return results

    def get_tool_detail(self, registry: Any, name: str, include_schema: bool = True) -> str:
        spec = _registry_get(registry, name)
        if spec is None:
            candidates = self.search(registry, name, max_results=5)
            if not candidates:
                return f"未找到工具 `{name}`。请调用 SearchTools 用能力关键词搜索可用工具。"
            lines = [f"未找到工具 `{name}`。相近候选："]
            lines.extend(f"- `{c['name']}`：{c['description']}" for c in candidates)
            return "\n".join(lines)

        loaded_now = False
        if self.config.get_tool_loads_tool:
            loaded_now = self.mark_loaded(spec.name)

        entry = ToolCatalogEntry.from_spec(spec)
        lines = [
            f"=== 工具 `{spec.name}` 完整说明 ===",
            "",
            "【说明】",
            spec.description or "(无描述)",
            "",
            "【属性】",
            f"- readonly: {entry.is_readonly}",
            f"- destructive: {entry.is_destructive}",
            f"- concurrency_safe: {entry.is_concurrency_safe}",
            f"- required: {entry.required_parameters}",
            f"- parameters: {entry.parameter_names}",
        ]
        if include_schema:
            lines.extend([
                "",
                "【参数 JSON Schema】",
                json.dumps(spec.parameters or {}, ensure_ascii=False, indent=2),
            ])
        if self.mode == "progressive":
            status = "已加入当前会话 loaded tools；下一轮模型请求会携带该工具 schema，可直接调用。"
            if not loaded_now and spec.name not in self.loaded_tools:
                status = "未加载：当前 loaded tools 已达到上限；需要先完成当前已加载工具的使用，或提高 max_loaded_tools。"
            lines.extend([
                "",
                "【加载状态】",
                status,
            ])
        return "\n".join(lines)

    def _effective_loaded_names(self, registry: Any) -> List[str]:
        available = {spec.name for spec in _registry_tools(registry)}
        names = [name for name in self.config.core_tools if name in available]
        for name in sorted(self.loaded_tools):
            if name in available and name not in names:
                names.append(name)
        return names


def make_search_tools_tool(loader: ToolLoader, registry: Any):
    from floodmind.tools.agent_tool import AgentTool
    from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy

    def _search_tools(query: str = "", max_results: int = 8) -> str:
        results = loader.search(registry, query, max_results=max_results)
        if not results:
            return "未找到匹配工具。请换一个能力关键词搜索。"
        lines = ["=== 工具搜索结果 ==="]
        for item in results:
            flags = []
            if item["destructive"]:
                flags.append("destructive")
            elif item["readonly"]:
                flags.append("readonly")
            flags.append("loaded" if item["loaded"] else "not-loaded")
            lines.append(
                f"- `{item['name']}` ({', '.join(flags)}): {item['description']} "
                f"required={item['required_parameters']} match={item['match']}"
            )
        lines.append("需要完整参数和使用方法时，调用 GetTool(tool_name=工具名)。")
        return "\n".join(lines)

    return AgentTool(
        name="SearchTools",
        description="按能力、工具名、描述或参数名搜索可用工具。只返回简短目录；需要完整参数时再调用 GetTool。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的能力或关键词，如 文件读取、MCP、记忆、网页搜索"},
                "max_results": {"type": "integer", "description": "最多返回多少个候选，默认 8"},
            },
            "required": ["query"],
        },
        func=_search_tools,
        is_readonly=True,
        is_destructive=False,
        is_concurrency_safe=True,
        permission_policy=ToolPermissionPolicy(policy_type="readonly"),
    )


def make_get_tool_tool(loader: ToolLoader, registry: Any):
    from floodmind.tools.agent_tool import AgentTool
    from floodmind.agent.runtime.contracts.permissions import ToolPermissionPolicy

    def _get_tool(tool_name: str = "", include_schema: bool = True) -> str:
        return loader.get_tool_detail(registry, str(tool_name or ""), include_schema=bool(include_schema))

    return AgentTool(
        name="GetTool",
        description="获取某个工具的完整说明、参数 JSON Schema、required 参数与风险属性。progressive 模式下调用后会加载该工具。",
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "工具名，如 Bash、Read、mcp:server:tool"},
                "include_schema": {"type": "boolean", "description": "是否返回完整参数 JSON Schema，默认 true"},
            },
            "required": ["tool_name"],
        },
        func=_get_tool,
        is_readonly=True,
        is_destructive=False,
        is_concurrency_safe=True,
        permission_policy=ToolPermissionPolicy(policy_type="readonly"),
    )
