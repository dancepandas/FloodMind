"""路径到架构分层的映射。

分层是人工约定的语义视图，用于把文件级别的图聚合为可读的架构图。
新增目录时在这里登记，保持单一映射源。
"""

from __future__ import annotations

# 顺序敏感：先匹配更具体的前缀。
_LAYER_RULES: tuple[tuple[str, str], ...] = (
    # 独立平台适配
    ("desktop/", "frontend-desktop"),
    ("floodmind/gateway/", "gateway"),
    # 工具与技能
    ("skills/", "skills"),
    ("floodmind/skills/", "skills"),
    ("floodmind/tools/", "tools"),
    # 评测与收益基线
    ("evals/", "evals"),
    ("benchmarks/", "benchmarks"),
    # 记忆
    ("floodmind/memory/", "memory"),
    ("floodmind/domain/memory/", "memory"),
    # 模型与配置
    ("floodmind/config/", "model-config"),
    ("floodmind/models/", "model-config"),
    ("floodmind/agent/native/model_client.py", "model-config"),
    ("floodmind/agent/native/transport.py", "model-config"),
    ("floodmind/agent/native/response_pipeline.py", "model-config"),
    ("floodmind/agent/native/capabilities.py", "model-config"),
    ("floodmind/agent/native/retry.py", "model-config"),
    # MCP
    ("floodmind/agent/mcp_client.py", "mcp"),
    # 运行时
    ("floodmind/agent/runtime/contracts/", "runtime-contracts"),
    ("floodmind/agent/runtime/services/", "runtime-services"),
    ("floodmind/agent/runtime/adapters/", "runtime-adapters"),
    ("floodmind/agent/runtime/", "runtime-kernel"),
    # SDK 与执行核心
    ("floodmind/agent/native/", "agent-core"),
    ("floodmind/agent/api.py", "sdk"),
    ("floodmind/agent/agent_registry.py", "sdk"),
    ("floodmind/agent/sdk_events.py", "sdk"),
    ("floodmind/__init__.py", "sdk"),
    ("floodmind/agent/", "agent-services"),
    # CLI 与扩展
    ("main.py", "cli"),
    ("floodmind/cli", "cli"),
    ("floodmind/scripts/", "cli"),
    ("floodmind/plugin/", "extension"),
    ("floodmind/profile/", "extension"),
    # 其它
    ("tests/", "tests"),
    ("scripts/", "tooling"),
    ("docs/", "docs"),
    ("floodmind/common/", "common"),
    ("floodmind/", "other"),
)

_LAYER_TITLES = {
    "sdk": "SDK 公共入口",
    "cli": "CLI/宿主入口",
    "agent-core": "Agent 执行核心",
    "agent-services": "Agent 辅助服务",
    "runtime-contracts": "运行时契约",
    "runtime-kernel": "Reducer/运行时内核",
    "runtime-services": "运行时服务",
    "runtime-adapters": "运行时适配器",
    "model-config": "模型与配置",
    "memory": "记忆与会话",
    "tools": "工具",
    "skills": "技能",
    "mcp": "MCP 集成",
    "extension": "扩展点",
    "common": "通用工具",
    "frontend-desktop": "Desktop",
    "gateway": "Gateway（HTTP/SSE 适配）",
    "tests": "测试",
    "evals": "内部评测集",
    "benchmarks": "收益基线",
    "tooling": "工程脚本",
    "docs": "文档",
    "other": "其它",
}


def layer_for_path(path: str) -> str:
    """返回文件所属的架构分层。"""

    normalized = path.replace("\\", "/")
    for prefix, layer in _LAYER_RULES:
        if normalized == prefix or normalized.startswith(prefix):
            return layer
    return "other"


def layer_title(layer: str) -> str:
    return _LAYER_TITLES.get(layer, layer)


def all_layer_titles() -> dict[str, str]:
    return dict(_LAYER_TITLES)
