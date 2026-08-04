"""Tests for progressive tool parameter loading."""

from floodmind.agent.native.tool_loading import (
    ToolLoader,
    ToolLoadingConfig,
    compact_prompt_catalog,
    make_get_tool_tool,
    make_search_tools_tool,
)
from floodmind.agent.runtime.contracts.tools import ToolSpec


class Registry:
    def __init__(self, tools):
        self._tools = {t.name: t for t in tools}

    def all(self):
        return list(self._tools.values())

    def get(self, name):
        return self._tools.get(name)

    def register(self, tool):
        self._tools[tool.name] = tool

    def tools_schema(self, names=None):
        tools = self.all() if names is None else [self._tools[n] for n in names if n in self._tools]
        return [t.to_openai_tool() for t in tools]


def _tool(name, desc, params=None, readonly=True, destructive=False):
    return ToolSpec(
        name=name,
        description=desc,
        parameters=params or {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"],
        },
        func=lambda **kw: "ok",
        is_readonly=readonly,
        is_destructive=destructive,
    )


def test_compact_prompt_catalog_omits_full_schema_in_eager_mode():
    reg = Registry([_tool("Read", "读取文件内容。用于查看本地文件。")])
    text = compact_prompt_catalog(reg)
    assert "tools schema" in text
    assert "`Read`" not in text
    assert "读取文件内容" not in text
    assert "properties" not in text
    assert "JSON Schema" not in text


def test_compact_prompt_catalog_lists_tools_only_in_progressive_mode():
    reg = Registry([_tool("Read", "读取文件内容。用于查看本地文件。")])
    text = compact_prompt_catalog(reg, mode="progressive")
    assert "`Read`" in text
    assert "读取文件内容" in text
    assert "properties" not in text
    assert "JSON Schema" not in text
    assert "GetTool" in text


def test_search_tools_matches_name_description_and_params_without_schema():
    reg = Registry([
        _tool("Read", "读取文件内容。"),
        _tool("WebSearch", "搜索网页资料。", {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        }),
    ])
    loader = ToolLoader(ToolLoadingConfig(mode="eager"))
    results = loader.search(reg, "query")
    assert results[0]["name"] == "WebSearch"
    assert results[0]["required_parameters"] == ["query"]
    assert "parameters" not in results[0]


def test_get_tool_returns_schema_and_marks_loaded_in_progressive():
    reg = Registry([_tool("Read", "读取文件内容。")])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["SearchTools", "GetTool"]))
    detail = loader.get_tool_detail(reg, "Read")
    assert "工具 `Read` 完整说明" in detail
    assert "参数 JSON Schema" in detail
    assert '"path"' in detail
    assert loader.is_executable("Read") is True


def test_progressive_request_tools_only_core_then_loaded():
    reg = Registry([
        _tool("SearchTools", "搜索工具。", {"type": "object", "properties": {}, "required": []}),
        _tool("GetTool", "获取工具详情。", {"type": "object", "properties": {}, "required": []}),
        _tool("Read", "读取文件内容。"),
    ])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["SearchTools", "GetTool"]))
    names = [t["function"]["name"] for t in loader.request_tools(reg)]
    assert names == ["SearchTools", "GetTool"]

    loader.get_tool_detail(reg, "Read")
    names = [t["function"]["name"] for t in loader.request_tools(reg)]
    assert names == ["SearchTools", "GetTool", "Read"]


def test_synthetic_tools_use_registry_live():
    reg = Registry([_tool("Read", "读取文件内容。")])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["SearchTools", "GetTool"]))
    search_tool = make_search_tools_tool(loader, reg)
    get_tool = make_get_tool_tool(loader, reg)
    reg.register(search_tool)
    reg.register(get_tool)

    search_text = search_tool.func(query="读取", max_results=5)
    assert "Read" in search_text
    detail = get_tool.func(tool_name="Read", include_schema=True)
    assert "参数 JSON Schema" in detail
    assert loader.is_executable("Read") is True
