# floodmind — SDK 包根

FloodMind 的可安装 Python 包。对外暴露稳定的 SDK 入口，对内组织 Agent 运行时、工具、记忆、配置、网关与技能等子系统。

## 公共入口

包根通过模块级 `__getattr__` 懒加载导出，导入 `floodmind` 不会触发 settings 初始化。

- `Agent` / `AgentTool` / `ToolRegistry` — 嵌入式 Agent 与工具封装
- `ModelClient` / `resolve_model` — 模型客户端与模型解析
- `Workspace` / `build_folder_workspace` / `build_workspace` — 工作区契约
- `Skill` / `SkillRegistry` / `create_skill_registry` — 技能体系
- `ToolLoadingConfig` / `GuardrailResult` / `HandoffTarget` — 运行配置与扩展点
- `DualMemory` — 会话历史
- `route_codec` / `ProviderCodec` — 厂商管线

## 子目录

| 目录 | 作用 |
| --- | --- |
| `agent/` | Agent SDK 门面、Native 执行引擎、Runtime 契约与服务 |
| `tools/` | AgentTool、内置工具与文件/记忆/搜索工具 |
| `memory/` | 会话历史、语义记忆、经验树、会话管理 |
| `skills/` | Skill 发现/注册/生命周期与内置技能 |
| `config/` | 配置加载、模型解析、Provider 注册、密钥加密 |
| `profile/` | SOUL.md 身份与系统提示词行为指导 |
| `plugin/` | Python 原生插件扩展 |
| `gateway/` | FastAPI + Chainlit 网关（可选能力） |
| `common/` | 跨子系统复用的低层基础设施 |
| `models/` | `ModelClient` 兼容导出层 |
| `scripts/` | 包内手工诊断脚本 |
| `cli.py` | `floodmind` 命令入口 |

## 约定

- 新增公共导出必须同时更新 `__init__.py` 中的 `_exports` 与 `__all__`。
- 新增包数据（技能、静态资源、模板）需同步 `pyproject.toml` 的 package-data。
- 包导入不得产生副作用：不初始化 settings、不配置 Matplotlib、不访问网络。
- 运行时的单一职责链路是 `Agent → NativeFloodAgent → executor → ToolExecutionService`，不要在下沉层复制编排逻辑。
