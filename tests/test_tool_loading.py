"""Tests for progressive tool parameter loading."""

from floodmind.agent.native.tool_loading import (
    DEFAULT_CORE_TOOLS,
    ToolLoader,
    ToolLoadingConfig,
    compact_prompt_catalog,
    make_get_tool_tool,
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


def test_eager_agent_does_not_advertise_tool_loader(tmp_path):
    from floodmind.agent.api import Agent
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.agent.runtime.contracts.workspace import Workspace

    model = ModelClient(api_key="mock-key", base_url="https://mock.api/v1",
                        model_name="mock-model", context_window=65536)
    for loading, expected in [(False, False), (True, True)]:
        agent = Agent(
            llm=model, bare=True, tools=[_tool("Read", "Read a file")],
            tool_loading=loading,
            workspace=Workspace.from_folder(tmp_path / str(loading)).ensure(),
        )
        for registry in (agent._agent._orchestrator_registry,
                         agent._agent._specialist_registry):
            names = {item["function"]["name"] for item in registry.tools_schema()}
            assert ("GetTool" in names) is expected
            assert "Read" in names


def test_specialist_runtime_respects_tool_loading_mode_and_isolates_loads(tmp_path):
    from floodmind.agent.api import Agent
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.agent.runtime.contracts.workspace import Workspace

    model = ModelClient(api_key="mock-key", base_url="https://mock.api/v1",
                        model_name="mock-model", context_window=65536)
    for loading, expected in [(False, False), (True, True)]:
        config = ToolLoadingConfig(
            mode="progressive" if loading else "eager", core_tools=["GetTool"],
        )
        agent = Agent(
            llm=model, bare=True, tools=[_tool("Lookup", "Lookup a file")],
            tool_loading=config,
            workspace=Workspace.from_folder(tmp_path / str(loading)).ensure(),
        )
        registry, loader = agent._agent._make_specialist_tool_runtime()
        other_registry, other_loader = agent._agent._make_specialist_tool_runtime()
        names = {item["function"]["name"] for item in registry.tools_schema()}
        assert ("GetTool" in names) is expected
        assert "Lookup" in names
        if loading:
            assert "Lookup" not in {
                item["function"]["name"] for item in loader.request_tools(registry)
            }
            registry.get("GetTool").func(tool_name="Lookup")
            assert "Lookup" in {
                item["function"]["name"] for item in loader.request_tools(registry)
            }
            assert "Lookup" not in {
                item["function"]["name"]
                for item in other_loader.request_tools(other_registry)
            }
        else:
            assert "Lookup" in {
                item["function"]["name"] for item in loader.request_tools(registry)
            }


def test_compact_prompt_catalog_omits_full_schema_in_eager_mode():
    reg = Registry([_tool("Read", "读取文件内容。用于查看本地文件。")])
    text = compact_prompt_catalog(reg)
    assert "tools schema" in text
    assert "`Read`" not in text
    assert "读取文件内容" not in text
    assert "properties" not in text
    assert "JSON Schema" not in text


def test_catalog_mode_injects_complete_tool_contract_without_loading():
    import json

    params = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "Exact station identifier"},
            "options": {"type": "object", "properties": {
                "units": {"type": "string", "enum": ["metric", "imperial"],
                          "default": "metric"},
            }},
        },
        "required": ["location"],
        "additionalProperties": False,
    }
    reg = Registry([_tool("StationLookup", "Find a station. Preserve its identifier.", params)])
    text = compact_prompt_catalog(reg, mode="catalog")
    assert "Find a station. Preserve its identifier." in text
    schema_text = text.split("```json\n", 1)[1].split("```", 1)[0]
    assert json.loads(schema_text) == params
    assert "GetTool" not in text
    loader = ToolLoader(ToolLoadingConfig(mode="catalog", core_tools=[]))
    assert loader.is_executable("StationLookup")
    assert loader.request_tools(reg)[0]["function"]["parameters"] == params


def test_catalog_agent_injects_contract_into_both_executors(tmp_path):
    from floodmind.agent.api import Agent
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.agent.runtime.contracts.workspace import Workspace

    agent = Agent(
        llm=ModelClient(api_key="mock-key", base_url="https://mock.api/v1",
                        model_name="mock-model", context_window=65536),
        bare=True, tools=[_tool("StationLookup", "Exact station identifier lookup.")],
        tool_loading=ToolLoadingConfig(mode="catalog"),
        workspace=Workspace.from_folder(tmp_path).ensure(),
    )
    for executor in (agent._agent._orchestrator_executor,
                     agent._agent._specialist_executor):
        prompt = "\n".join(executor.system_prompts)
        assert "Exact station identifier lookup." in prompt
        assert '"required": [' in prompt
        assert '"path"' in prompt
    registry, loader = agent._agent._make_specialist_tool_runtime()
    assert registry.get("GetTool") is None
    assert loader.is_executable("StationLookup")


def test_bare_catalog_refresh_preserves_host_and_replaces_old_contract(tmp_path):
    from floodmind.agent.api import Agent
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.agent.runtime.contracts.workspace import Workspace

    agent = Agent(
        llm=ModelClient(api_key="mock-key", base_url="https://mock.api/v1",
                        model_name="mock-model", context_window=65536),
        bare=True, system_prompt="HOST-MUST-SURVIVE",
        tools=[_tool("StationLookup", "OLD-CONTRACT")],
        tool_loading=ToolLoadingConfig(mode="catalog"),
        workspace=Workspace.from_folder(tmp_path).ensure(),
    )
    runtime = agent._agent
    runtime._skill_catalog = "SKILL-CONTRACT-INITIAL"
    runtime._rebuild_system_prompts()
    for executor in (runtime._orchestrator_executor, runtime._specialist_executor):
        assert "SKILL-CONTRACT-INITIAL" in "\n".join(executor.system_prompts)
    runtime._skill_catalog = "SKILL-CONTRACT-UPDATED"
    for registry in (runtime._orchestrator_registry, runtime._specialist_registry):
        registry.get("StationLookup").description = "UPDATED-CONTRACT"
    runtime._rebuild_system_prompts()
    runtime._rebuild_system_prompts()
    for executor in (runtime._orchestrator_executor, runtime._specialist_executor):
        prompt = "\n".join(executor.system_prompts)
        assert prompt.count("HOST-MUST-SURVIVE") == 1
        assert prompt.count("UPDATED-CONTRACT") == 1
        assert "OLD-CONTRACT" not in prompt
        assert prompt.count("SKILL-CONTRACT-UPDATED") == 1
        assert "SKILL-CONTRACT-INITIAL" not in prompt
    runtime._skill_catalog = ""
    runtime._rebuild_system_prompts()
    for executor in (runtime._orchestrator_executor, runtime._specialist_executor):
        prompt = "\n".join(executor.system_prompts)
        assert "SKILL-CONTRACT" not in prompt
        assert "HOST-MUST-SURVIVE" in prompt
        assert "UPDATED-CONTRACT" in prompt


def test_get_tool_real_end_to_end_malformed_key():
    """真实 GetTool 工具 + 畸形键（键名带尾引号）端到端：应清洗后正常执行。"""
    from floodmind.agent.runtime.contracts.permissions import PermissionBehavior, PermissionDecision
    from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
    from floodmind.agent.runtime.contracts.tools import ToolCall
    from floodmind.tools.session_context import set_runtime_context
    from unittest.mock import MagicMock
    from floodmind.agent.runtime.services.tool_execution_service import ToolExecutionService

    reg = Registry([])
    loader = ToolLoader()
    get_tool_spec = make_get_tool_tool(loader, reg).to_tool_spec()
    reg.register(get_tool_spec)
    reg.register(_tool("Read", "读取文件内容。"))

    permission_service = MagicMock()
    permission_service.check.return_value = PermissionDecision(behavior=PermissionBehavior.ALLOW)
    set_runtime_context(RuntimeContext("c", "t", "r", "th", "turn", permission_service=permission_service))
    svc = ToolExecutionService(permission_service=permission_service)
    call = ToolCall(id="c1", name="GetTool", arguments={'tool_name"': "Read"})

    result = svc.execute(call, context=None, registry=reg, journal_authority=object())
    set_runtime_context(None)

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
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["GetTool"]))
    detail = loader.get_tool_detail(reg, "Read")
    assert "工具 `Read` 完整说明" in detail
    assert "参数 JSON Schema" in detail
    assert '"path"' in detail
    assert loader.is_executable("Read") is True


def test_progressive_request_tools_only_core_then_loaded():
    reg = Registry([
        _tool("GetTool", "获取工具详情。", {"type": "object", "properties": {}, "required": []}),
        _tool("Read", "读取文件内容。"),
    ])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["GetTool"]))
    names = [t["function"]["name"] for t in loader.request_tools(reg)]
    assert names == ["GetTool"]

    loader.get_tool_detail(reg, "Read")
    names = [t["function"]["name"] for t in loader.request_tools(reg)]
    assert names == ["GetTool", "Read"]


def test_short_description_strips_param_hint_prefix():
    from floodmind.agent.native.tool_loading import short_description

    assert short_description("[必填] command: 要执行的 shell 命令。不要嵌套 shell。") == "要执行的 shell 命令"
    assert short_description("[可选] workdir: 工作目录。用于指定路径。") == "工作目录"
    assert short_description("网络搜索。[必填] query: 搜索关键词。") == "网络搜索"
    assert short_description("读取文件内容，支持文本与二进制。用于查看本地文件。") == "读取文件内容，支持文本与二进制"


def test_default_core_tools_exclude_searchtools():
    """SearchTools 已移除：默认 core tools 只含 GetTool/GetSkill，工具发现完全靠提示目录 + GetTool。"""
    assert "SearchTools" not in DEFAULT_CORE_TOOLS
    assert "GetTool" in DEFAULT_CORE_TOOLS
    assert "GetSkill" in DEFAULT_CORE_TOOLS


def test_progressive_catalog_lists_tools_and_guides_get_tool():
    """progressive 提示目录直接列全量工具名 + 基本描述，并引导用 GetTool 取参数；不再引导 SearchTools。"""
    reg = Registry([
        _tool("Read", "读取文件内容。用于查看本地文件。"),
        _tool("Bash", "[必填] command: 要执行的 shell 命令。不要嵌套 shell。", {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }, readonly=False, destructive=True),
    ])
    text = compact_prompt_catalog(reg, mode="progressive")
    assert "- `Read`" in text
    assert "读取文件内容" in text
    assert "- `Bash` [destructive]" in text
    assert "要执行的 shell 命令" in text
    assert "GetTool" in text
    assert "SearchTools" not in text
    assert "required=" not in text


def test_synthetic_tools_use_registry_live():
    reg = Registry([_tool("Read", "读取文件内容。")])
    loader = ToolLoader(ToolLoadingConfig(mode="progressive", core_tools=["GetTool"]))
    get_tool = make_get_tool_tool(loader, reg)
    reg.register(get_tool)

    detail = get_tool.func(tool_name="Read", include_schema=True)
    assert "参数 JSON Schema" in detail
    assert loader.is_executable("Read") is True
