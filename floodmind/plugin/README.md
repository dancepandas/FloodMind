# floodmind/plugin — Python 原生插件

以 Python 代码扩展工具的深度集成方式，与 MCP 互补：插件适合进程内、低延迟的系统集成，MCP 适合跨进程、可热插拔的工具服务。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `base.py` | `FloodmindPlugin` 基类：元信息、`on_load` / `on_unload` / `on_agent_init`、`get_tools()`、`get_hooks()` |
| `loader.py` | `PluginLoader`：扫描插件目录、加载单文件或目录插件、幂等发现 |

## 发现路径

- `~/.floodmind/plugins/`
- `./.floodmind/plugins/`

单文件插件跳过 `_` / `.` 开头的文件；目录插件必须包含 `plugin.json`，`entry` 默认 `"plugin"`。

## 约定

- 插件类必须可无参实例化，并由 loader 负责创建。
- 单个插件加载或回调异常只记 warning，不阻断 Agent 启动。
- `discover()` 按源文件路径去重，重复调用不重复实例化。
- 插件注册的工具进入 orchestrator registry；hooks 接入 EventBus。
