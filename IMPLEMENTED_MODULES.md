# FloodMind 项目功能模块说明

本文档按功能模块梳理当前项目已实现能力和规划实现内容，并明确指向对应实现文件，便于后续维护、排查和二次开发。

# 一、已完成功能

## 1. 启动入口与运行进程

### Web 服务入口
- 功能：启动 Flask 后端，提供聊天、文件、会话、日志、定时任务查询等 API，并托管前端静态资源。
- 实现文件：`web_server.py`
- 关键位置：`web_server.py` 中的 `if __name__ == '__main__'` 启动逻辑。
- 常用命令：`python web_server.py`

### 后台定时任务调度进程
- 功能：独立常驻进程，启动后先扫描一次到期定时任务，之后每个整点唤醒，执行到期任务。
- 实现文件：`scheduler.py`
- 关键函数：`main`、`run_once`、`execute_task`、`seconds_until_next_hour`
- 常用命令：`python scheduler.py`
- 单次扫描命令：`python scheduler.py --once`

### 命令行交互入口
- 功能：提供终端内 Agent 对话能力，适合开发和调试。
- 实现文件：`main.py`
- 关键函数：`init_agent`、`main`
- 常用命令：`python main.py`

## 2. 全局配置模块

### 环境变量和全局配置
- 功能：集中读取 API、Qwen、Agent、RAG 等配置；设置 HuggingFace 镜像、IPv4、SSL 相关运行参数。
- 实现文件：`config/settings.py`
- 配置类：`APIConfig`、`QwenConfig`、`AgentConfig`、`RAGConfig`、`Settings`

### 配置包导出
- 功能：提供配置模块包级导出。
- 实现文件：`config/__init__.py`

## 3. LLM 服务模块

### Qwen / DashScope 模型封装
- 功能：封装 Qwen OpenAI 兼容接口，支持普通输出、流式输出、推理模式和搜索能力配置。
- 实现文件：`models/qwen_llm_service.py`
- 包导出文件：`models/__init__.py`

## 4. Agent 编排模块

### FloodAgent 主体
- 功能：核心智能体，负责意图分析、创建计划、调用工具、分发执行单元、汇总结果、流式输出。
- 实现文件：`agent/flood_agent.py`
- 关键类：`FloodAgent`
- 关键能力：`create_plan`、`delegate_execution_specialist`、`run`、`stream`、`chat`

### Dispatcher + Execution Specialist 架构
- 功能：调度 Agent 负责规划和分发，执行单元负责单步落地任务。
- 实现文件：`agent/flood_agent.py`
- 关键位置：`SYSTEM_PROMPT`、`EXECUTION_SPECIALIST_PROMPT`、`_build_delegation_tools`、`_run_specialist_task`

### 流式 Agent Loop
- 功能：通过队列和后台线程把 Agent 推理、工具状态、工作流步骤和最终回答实时推送给 Web API。
- 实现文件：`agent/flood_agent.py`
- 关键类/函数：`_FunctionsStreamCallback`、`stream`

### Agent 包惰性导入
- 功能：避免导入 `agent.scheduled_task_runtime` 时触发完整 Agent 和模型配置初始化。
- 实现文件：`agent/__init__.py`
- 关键函数：`__getattr__`

## 5. Agent Runtime Core

### Context Runtime
- 功能：装配项目规则、系统环境、当前时间等上下文，并提供上下文缓存和预算裁剪能力。
- 实现文件：`agent/context_runtime.py`
- 关键类：`ContextRuntime`

### Task Runtime
- 功能：为 Agent Loop 内部任务提供状态机，记录任务创建、运行、完成、失败、终止等状态。
- 实现文件：`agent/task_runtime.py`
- 关键类/枚举：`TaskTracker`、`Task`、`TaskStatus`、`TaskType`、`TaskResult`

### Scheduled Task Runtime
- 功能：管理后台定时任务，支持 JSON 持久化、一次性任务、每日重复任务、到期认领、跳过过期任务、执行结果回写、本次新增产物记录。
- 实现文件：`agent/scheduled_task_runtime.py`
- 关键类：`ScheduledTaskRuntime`
- 默认数据文件：`data/scheduled_tasks.json`
- 可配置环境变量：`SCHEDULED_TASKS_FILE`

## 6. 工具运行时模块

### AgentTool 统一工具抽象
- 功能：为所有 Agent 工具提供统一结构，包含只读/破坏性/并发安全/中断行为/权限检查/输入校验等元数据。
- 实现文件：`tools/agent_tool.py`
- 关键类/函数：`AgentTool`、`ToolRegistry`、`PermissionManager`、`build_agent_tool`、`check_path_permission`、`check_dangerous_command`

### 基础工具集合
- 功能：提供 skill 查询、脚本执行、命令执行、文件写入、知识检索、记忆检索、产物搜索等 Agent 可调用工具。
- 实现文件：`tools/base_tools.py`
- 关键工具：`get_skill`、`run_script`、`exec_bash`、`exec_python_file`、`write_text_file`、`search_artifacts`、`read_artifact`、`knowledge_search`、`web_search`、`add_memory`、`search_memory`

### 定时任务 Agent 工具
- 功能：允许用户用自然语言要求 Agent 创建、查询、取消后台定时任务。
- 实现文件：`tools/base_tools.py`
- 关键工具：`create_scheduled_task`、`list_scheduled_tasks`、`cancel_scheduled_task`
- 注册文件：`tools/__init__.py`
- Agent 提示接入文件：`agent/flood_agent.py`

### 工具包导出
- 功能：统一导出工具函数、运行时类和注册入口。
- 实现文件：`tools/__init__.py`

## 7. Web 后端 API 模块

### Flask 应用与静态资源托管
- 功能：创建 Flask 应用，启用 CORS，根据构建结果选择 React 前端或旧版静态页。
- 实现文件：`web_server.py`
- 关键变量：`REACT_DIST_DIR`、`LEGACY_WEB_DIR`、`USE_REACT_FRONTEND`、`STATIC_WEB_DIR`

### 聊天 API
- 功能：接收用户消息，构造会话上下文、上传文件上下文，调用 `FloodAgent.stream` 输出 NDJSON 流。
- 实现文件：`web_server.py`
- API：`POST /api/chat`
- 关键函数：`chat`、`stream_json_line`、`init_stream_snapshot`、`finish_stream_snapshot`

### Agent 初始化和配置 API
- 功能：初始化会话 Agent，管理搜索、RAG、推理模式等会话配置。
- 实现文件：`web_server.py`
- API：`POST /api/init`、`POST /api/session/config`、`GET /api/config`

### 会话管理 API
- 功能：列出、读取、保存、删除、清理会话，并恢复进行中的流式快照。
- 实现文件：`web_server.py`
- API：`GET /api/sessions`、`GET /api/sessions/<session_id>`、`POST /api/sessions/save`、`DELETE /api/sessions/<session_id>`、`POST /api/sessions/cleanup`、`GET /api/sessions/stats`

### 上传文件 API
- 功能：上传、列出、预览、下载、删除当前会话文件。
- 实现文件：`web_server.py`
- API：`POST /api/upload`、`GET /api/files`、`GET /api/files/<file_id>/preview`、`GET /api/files/<file_id>/download`、`DELETE /api/files/<file_id>`

### 输出产物和下载 API
- 功能：下载会话输出目录中的单文件、打包下载全部输出、过滤内部路径、生成前端 artifact 事件。
- 实现文件：`web_server.py`
- API：`GET /api/sessions/<session_id>/outputs/<path:filename>`、`GET /api/sessions/<session_id>/outputs/download`、`GET /api/download/<path:file_path>`
- 关键函数：`build_artifact_event`、`resolve_artifact_references`、`save_session_artifact_events`

### 定时任务 Web API
- 功能：给前端提供定时任务列表、详情、修改、删除和最近一次新增产物查询。
- 实现文件：`web_server.py`
- API：`GET /api/scheduled-tasks`、`GET /api/scheduled-tasks/<task_id>`、`PATCH /api/scheduled-tasks/<task_id>`、`DELETE /api/scheduled-tasks/<task_id>`、`GET /api/scheduled-tasks/<task_id>/artifacts`
- Runtime 文件：`agent/scheduled_task_runtime.py`

### 运行状态和控制 API
- 功能：健康检查、日志下载、记忆统计、手动记忆心跳、会话暂停/恢复/状态查询。
- 实现文件：`web_server.py`
- API：`GET /api/health`、`GET /api/logs`、`GET /api/memory/stats`、`POST /api/memory/heartbeat`、`POST /api/session/pause`、`POST /api/session/resume`、`GET /api/session/status`

## 8. 后台定时任务模块

### 用户自然语言创建任务
- 功能：用户在聊天中描述“每天早上 8 点执行某任务”，Agent 识别为定时任务需求并调用工具写入任务列表。
- Agent 接入文件：`agent/flood_agent.py`
- 工具实现文件：`tools/base_tools.py`
- Runtime 文件：`agent/scheduled_task_runtime.py`

### 到期任务执行
- 功能：后台进程使用任务自己的 `session_id` 创建/获取 Agent，将 `command` 作为自然语言交给 Agent 执行。
- 实现文件：`scheduler.py`
- 关键函数：`execute_task`、`get_or_create_agent`、`create_agent_for_session`

### 本次新增产物识别
- 功能：任务执行前后扫描该会话 `outputs` 目录，差集作为本次任务新增文件写入 `artifacts`。
- 实现文件：`scheduler.py`
- Runtime 记录文件：`agent/scheduled_task_runtime.py`
- 前端展示文件：`web/src/features/scheduler/components/ScheduledTasksPanel.tsx`

## 9. 会话与文件存储模块

### SessionManager
- 功能：管理会话目录、上传目录、输出目录、记忆目录、会话清理线程、会话元数据和 Agent 实例缓存。
- 实现文件：`memory/session_manager.py`
- Web 接入文件：`web_server.py`
- Scheduler 接入文件：`scheduler.py`

### 会话上下文变量
- 功能：记录当前 `session_id` 和 `SESSION_OUTPUT_DIR`，供工具执行脚本、写文件和生成产物时定位输出目录。
- 实现文件：`tools/base_tools.py`
- 关键函数：`set_session_context`、`get_current_session_id`、`get_current_session_output_dir`

## 10. 记忆模块

### 简单记忆
- 功能：为命令行或基础场景提供短历史对话记忆。
- 实现文件：`memory/simple_memory.py`

### 双层记忆
- 功能：提供短期对话、长期记忆、压缩归纳、心跳归纳和历史检索。
- 实现文件：`memory/dual_memory.py`
- Web 接入文件：`web_server.py`
- Agent 接入文件：`agent/flood_agent.py`

### 全局搜索和记忆导出
- 功能：支持跨会话或全局记忆搜索能力。
- 实现文件：`memory/global_search.py`

### 记忆包导出
- 功能：统一导出记忆类和 SessionManager。
- 实现文件：`memory/__init__.py`

## 11. RAG 知识检索模块

### Embedding
- 功能：封装知识库向量化所需的 embedding 模型。
- 实现文件：`rag/embeddings.py`

### Vector Store
- 功能：管理 ChromaDB 向量库持久化、写入和读取。
- 实现文件：`rag/vector_store.py`

### Retriever
- 功能：为 Agent 工具提供知识检索入口。
- 实现文件：`rag/retriever.py`
- 工具接入文件：`tools/base_tools.py` 中的 `knowledge_search`

### 知识库构建脚本
- 功能：从文档构建或更新本地知识库。
- 实现文件：`scripts/build_knowledge_base.py`

## 12. Skill 系统模块

### Skill 发现与注册
- 功能：自动扫描 `skills/` 和 `.claude/skills/` 下的 `SKILL.md`，生成技能注册表和技能目录。
- 实现文件：`skills/__init__.py`
- Skill 数据结构文件：`skills/base.py`
- Agent 接入文件：`agent/flood_agent.py`

### Skill 执行入口
- 功能：Agent 通过 `get_skill` 查看说明，通过 `run_script` 运行 skill 内脚本。
- 工具实现文件：`tools/base_tools.py`
- 脚本目录约定：`skills/<skill-name>/scripts/`

### 荆州水文模型 Skill
- 功能：运行荆州水文模型并导出结果。
- 实现文件：`skills/jingzhou-hydro/scripts/run_jingzhou_hydro_model.py`
- 导出脚本：`skills/jingzhou-hydro/scripts/export_jingzhou_hydro_result_to_excel.py`

### 敖江水文模型 Skill
- 功能：运行敖江水文模型相关预测任务。
- 实现文件：`skills/aojiang-hydro/scripts/run_aojiang_hydro_model.py`

### TSLM / Chronos 预测 Skill
- 功能：提供时序预测能力。
- 实现文件：`skills/TSLM/scripts/flood_prediction.py`
- 公共管线文件：`skills/chronos_pipeline.py`

### 水文案例客户端
- 功能：调用或封装水文案例数据接口。
- 实现文件：`skills/hydro_case_client.py`

### Excel Skill
- 功能：生成 Excel、预览表格、重新计算工作簿、处理 Office OpenXML。
- 实现文件：`skills/xlsx/scripts/create_excel.py`、`skills/xlsx/scripts/preview_data.py`、`skills/xlsx/scripts/recalc.py`
- Office 工具文件：`skills/xlsx/scripts/office/*.py`

### Word 文档 Skill
- 功能：创建 Word 文档、批注、接受修订、处理 Office OpenXML。
- 实现文件：`skills/docx/scripts/create_docx.py`、`skills/docx/scripts/comment.py`、`skills/docx/scripts/accept_changes.py`
- Office 工具文件：`skills/docx/scripts/office/*.py`

### CSV 预览 Skill
- 功能：预览 CSV 数据内容。
- 实现文件：`skills/csv/scripts/preview_data.py`

## 13. 前端 React 应用模块

### 应用入口和路由
- 功能：创建 React 应用、React Query Provider 和路由。
- 实现文件：`web/src/main.tsx`、`web/src/App.tsx`

### 主页面布局
- 功能：组合左侧会话栏、中间聊天区、右侧上下文面板。
- 实现文件：`web/src/pages/AgentPage.tsx`

### 应用状态 Hook
- 功能：管理会话、消息、上传文件、流式事件、工作流状态、预览状态、暂停恢复等前端核心状态。
- 实现文件：`web/src/hooks/useAgentApp.ts`

### API 客户端
- 功能：封装后端 API 请求、聊天流请求、文件上传下载、会话操作、定时任务查询删除。
- 实现文件：`web/src/api/client.ts`、`web/src/api/agent.ts`

### 聊天区域
- 功能：展示消息、思考块、工具状态、工作流内容、输入框和上传入口。
- 实现文件：`web/src/features/chat/components/ChatArea.tsx`、`web/src/features/chat/components/ChatMessage.tsx`、`web/src/features/chat/components/ChatComposer.tsx`
- 流事件解析：`web/src/features/chat/lib/stream-events.ts`
- 消息块处理：`web/src/features/chat/lib/message-blocks.ts`

### 文档预览
- 功能：支持 PDF、Excel、Docx 等产物预览。
- 实现文件：`web/src/features/chat/components/DocumentPreviewDialog.tsx`
- 预览组件：`web/src/features/chat/components/previews/PdfPreview.tsx`、`web/src/features/chat/components/previews/ExcelPreview.tsx`、`web/src/features/chat/components/previews/DocxPreview.tsx`

### 左侧会话栏
- 功能：展示最近会话、新建会话、切换会话、删除会话。
- 实现文件：`web/src/features/sidebar/components/Sidebar.tsx`

### 右侧上下文面板
- 功能：展示上传文件、文件预览、当前执行计划、定时任务面板。
- 实现文件：`web/src/features/context/components/ContextPanel.tsx`

### 定时任务前端面板
- 功能：展示当前会话定时任务概览，包括任务状态、下次执行时间、最近错误摘要，支持刷新和删除任务。
- 实现文件：`web/src/features/scheduler/components/ScheduledTasksPanel.tsx`
- API 文件：`web/src/api/agent.ts`
- 类型文件：`web/src/types/app.ts`

### 定时任务结果窗口
- 功能：点击任务卡片上的"查看结果"按钮，打开 Modal 弹窗，集中展示该任务最近一次执行详情。
- 展示内容：任务命令、执行状态、最近执行时间、完成时间、下次执行时间、Agent 文本执行结果（可滚动）、错误信息、本次新增产物下载。
- 实现文件：`web/src/features/scheduler/components/ScheduledTaskResultDialog.tsx`
- 触发入口：`web/src/features/scheduler/components/ScheduledTasksPanel.tsx` 中的 `查看结果` 按钮
- UI 组件：`web/src/components/ui/dialog.tsx`

### 前端类型定义
- 功能：定义消息、文件、工作流、会话、定时任务等 TypeScript 类型。
- 实现文件：`web/src/types/app.ts`、`web/src/types/agent.ts`

### 样式和 UI 基础组件
- 功能：TailwindCSS 主题变量、全局样式、通用 UI 组件。
- 实现文件：`web/src/index.css`
- UI 组件目录：`web/src/components/ui/`

## 14. 日志和运行数据模块

### 日志输出
- 功能：Web 服务、主程序、后台调度器按日期滚动写日志。
- Web 日志实现：`web_server.py`
- Scheduler 日志实现：`scheduler.py`
- CLI 日志实现：`main.py`
- 默认日志目录：`logs/`

### 运行数据目录
- 功能：存放会话、上传文件、输出文件、向量库、定时任务、工具错误记忆等运行态数据。
- 目录：`data/`
- 会话数据使用文件：`memory/session_manager.py`
- 定时任务数据使用文件：`agent/scheduled_task_runtime.py`
- RAG 数据使用文件：`rag/vector_store.py`
- 工具错误记忆使用文件：`tools/base_tools.py`

## 15. 部署和运行方式

### Windows Server 当前推荐常驻方式
- Web 服务：`python web_server.py`
- 后台定时任务：`python scheduler.py`
- 可选命令行交互：`python main.py`

### 前端构建
- 功能：构建 React 前端到 `web/dist`，供 Flask 托管。
- 配置文件：`web/package.json`、`web/vite.config.ts`
- 构建命令：`npm run build`

### 依赖配置
- Python 依赖由项目环境管理。
- 前端依赖配置文件：`web/package.json`

# 二、规划实现内容

## 1. 定时任务历史执行记录

### 背景
- 第一版结果窗口已实现，展示最近一次执行结果。
- 每日任务会重复执行，当前 `last_result` 和 `artifacts` 只保留最近一次结果。
- 用户可能需要查看每天或每次运行的历史结果。

### 目标
- 增加历史执行记录，支持查看每天或每次运行的结果。
- 后端字段：在任务对象中新增 `runs`。
- 保留 `last_result`、`last_error`、`artifacts`，用于快速展示最近一次结果。

### 第二版后端数据结构规划
```json
"runs": [
  {
    "run_id": "run_xxx",
    "started_at": "2026-04-27T08:00:00",
    "finished_at": "2026-04-27T08:03:12",
    "status": "completed",
    "result": "Agent输出结果...",
    "error": "",
    "artifacts": []
  }
]
```

### 涉及文件
- `agent/scheduled_task_runtime.py`：在 `complete_task` 中追加一条 run 记录，保留最近 N 条，例如 20 条，避免 `data/scheduled_tasks.json` 无限增长。
- `web/src/types/app.ts`：新增 `ScheduledTaskRun`，并在 `ScheduledTask` 中增加 `runs?: ScheduledTaskRun[]`。
- `web/src/features/scheduler/components/ScheduledTaskResultDialog.tsx`：增加“最近结果 / 历史记录”切换，历史记录展示每次运行状态、时间、结果、错误和产物。

### 执行顺序
- 第一步：后端 `agent/scheduled_task_runtime.py` 在 `complete_task` 中追加 `runs` 记录。
- 第二步：前端 `web/src/types/app.ts` 新增 `ScheduledTaskRun`。
- 第三步：前端 `ScheduledTaskResultDialog.tsx` 增加"最近结果 / 历史记录"切换。
- 第四步：运行 `npm run build` 验证。

## 2. 历史会话产物恢复预览与下载

### 背景
- 当前正常对话中生成的文件可以实时预览和下载。
- 关闭网页、刷新页面或重新打开历史会话后，原本生成的文件卡片可能消失，导致无法继续预览或下载。
- 文件本体通常仍保存在 `data/sessions/<session_id>/outputs/` 下，问题主要发生在前端历史消息恢复和 artifact 元数据恢复链路。

### 已确认根因
- 后端会把最终确认的产物事件写入 `data/sessions/<session_id>/approved_artifacts.json`。
- `web_server.py` 中 `_sanitize_artifact_event` 会移除 `filepath` 字段，避免向前端暴露系统路径。
- 前端 `web/src/hooks/useAgentApp.ts` 中 `normalizeArtifact` 当前要求 `filepath` 必须是字符串；历史恢复时 artifact 缺少 `filepath` 会被直接过滤。
- 因此，刷新或重开会话后，`fetchSession` 虽然返回 `artifacts`，但前端没有把它们恢复到消息中。

### 连带问题
- `web_server.py` 中 `save_session_artifact_events` 当前采用覆盖写入方式，多轮对话生成产物时可能只保留最近一轮的 approved artifacts。
- 前端恢复逻辑当前会把所有历史 artifacts 统一挂到最后一条 assistant 消息，无法精确恢复到文件生成时对应的回复。
- 如果历史 artifact 缺少 `download_url` 或 `image_url`，当前后端没有统一补齐兼容字段。

### 目标
- 刷新网页、关闭网页后重新打开、切换历史会话时，已生成文件仍能显示文件卡片。
- 历史文件卡片仍支持预览和下载。
- 多轮对话生成的多个文件都应保留，不应只保留最近一轮。
- 不向前端暴露服务器完整路径，继续保持路径脱敏和安全边界。

### 修复规划
- 前端 `web/src/hooks/useAgentApp.ts`：修改 `normalizeArtifact`，不再要求 `filepath` 必填；无 `filepath` 时使用 `download_url`、`image_url` 或 `filename` 构造前端去重 key。
- 前端 `web/src/features/chat/lib/message-blocks.ts`：调整 `attachArtifact` 去重逻辑，兼容无 `filepath` 的历史 artifact。
- 后端 `web_server.py`：修改 `save_session_artifact_events` 为读取旧列表、合并新列表、按 `download_url` 或 `type + filename` 去重后写回，避免覆盖历史产物。
- 后端 `web_server.py`：在 `list_session_artifact_events` 中对历史 artifact 做兼容补齐，缺少 `download_url` 时根据 `session_id + filename` 生成 `/api/sessions/<session_id>/outputs/<filename>`；图片缺少 `image_url` 时同步补齐。
- 中期增强：保存 artifact 时增加 `message_id` 或 `assistant_message_index`，前端恢复时将产物挂回对应 assistant 消息，而不是全部挂到最后一条。

### 涉及文件
- `web_server.py`：`_sanitize_artifact_event`、`list_session_artifact_events`、`save_session_artifact_events`、`GET /api/sessions/<session_id>`。
- `web/src/hooks/useAgentApp.ts`：`normalizeArtifact` 和历史会话恢复逻辑。
- `web/src/features/chat/lib/message-blocks.ts`：`attachArtifact` 去重逻辑。
- `web/src/features/chat/components/ChatMessage.tsx`：文件卡片预览和下载入口。
- `web/src/features/chat/components/DocumentPreviewDialog.tsx`：文档预览和下载按钮。

### 验证用例
- 在一个会话中生成 `xlsx` 文件，确认实时预览和下载正常。
- 刷新页面后重新进入同一会话，确认 `xlsx` 文件卡片仍存在并可下载。
- 在同一会话继续生成 `png` 图片，刷新后确认 `xlsx` 和 `png` 都存在。
- 关闭浏览器后重新打开项目页面，确认历史会话文件仍可预览和下载。
- 检查返回给前端的 artifact 不包含系统完整路径。
