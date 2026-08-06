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


def test_get_tool_real_end_to_end_malformed_key():
    """真实 GetTool 工具 + 畸形键（键名带尾引号）端到端：应清洗后正常执行。"""
    from floodmind.agent.runtime.contracts.tools import ToolCall
    from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService

    reg = Registry([])
    loader = ToolLoader()
    get_tool_spec = make_get_tool_tool(loader, reg).to_tool_spec()
    reg.register(get_tool_spec)
    reg.register(_tool("Read", "读取文件内容。"))

    svc = ToolExecutionService()
    call = ToolCall(id="c1", name="GetTool", arguments={'tool_name"': "Read"})

    result = svc.execute(call, context=None, registry=reg)

    assert result.status == "completed"
    assert "`Read`" in result.content
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


def test_short_description_strips_param_hint_prefix():
    from floodmind.agent.native.tool_loading import short_description

    assert short_description("[必填] command: 要执行的 shell 命令。不要嵌套 shell。") == "要执行的 shell 命令"
    assert short_description("[可选] workdir: 工作目录。用于指定路径。") == "工作目录"
    assert short_description("网络搜索。[必填] query: 搜索关键词。") == "网络搜索"
    assert short_description("读取文件内容，支持文本与二进制。用于查看本地文件。") == "读取文件内容，支持文本与二进制"


def test_search_tools_output_clean_catalog_style():
    reg = Registry([
        _tool("Read", "读取文件内容。用于查看本地文件。"),
        _tool("Bash", "[必填] command: 要执行的 shell 命令。不要嵌套 shell。", {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }, readonly=False, destructive=True),
        _tool("WebSearch", "网络搜索。", {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }),
    ])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["SearchTools", "GetTool"]))
    search_tool = make_search_tools_tool(loader, reg)
    text = search_tool.func(query="", max_results=8)

    # 只列工具名 + 基本描述，去掉 required= / match= / not-loaded 噪音
    assert "=== 工具搜索结果 ===" in text
    assert "- `Read`" in text
    assert "读取文件内容" in text
    assert "要执行的 shell 命令" in text
    assert "required=" not in text
    assert "match=" not in text
    assert "not-loaded" not in text
    # GetTool 指引仍保留
    assert "GetTool" in text


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
