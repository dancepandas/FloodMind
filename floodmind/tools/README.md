# floodmind/tools — Agent 工具层

工具的定义、注册、执行入口与内置工具集合。宿主自定义工具通过 `AgentTool` 或 `build_agent_tool` 接入。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `agent_tool.py` | `AgentTool`、`ToolRegistry`、`build_agent_tool` |
| `base_tools.py` | 内置工具：Bash、Read/Write/Edit、Glob/Grep、WebFetch、GetTool/GetSkill、后台任务、记忆与搜索等 |
| `file_tools.py` | 文件读写与补丁类工具 |
| `memory_tools.py` | 记忆、经验与会话检索工具 |
| `session_context.py` | 当前会话/运行上下文的 contextvar 注入 |
| `__init__.py` | 公共导出 |

## 约定

- 工具只做业务逻辑与必要的输入保护；权限检查统一由 `ToolExecutionService` 负责。
- 内置工具用固定文本前缀表达预期失败（如“错误：文件不存在”），执行层据此归一为 `status=error`；新增工具需复用同一约定（见 `floodmind/common/tool_output.py`）。
- PowerShell 命令由 `base_tools` 注入 `$ErrorActionPreference = 'Stop'`；模型显式设置时不覆盖。
- 子进程环境先经 `credential_env` 过滤凭证。
- 工具描述由 `ToolRegistry` 注入系统提示词，不要在 profile guidance 中重复。
