# Changelog

All notable changes to FloodMind are documented in this file.

## [2.1.9] - 2026-08-30

> 折叠栏设计 v2：默认折叠 + 下方实时显示最近 2 条过程活动摘要。

### Changed

- **折叠栏默认折叠（可点开）**：用户偏好"折叠屏就是折叠屏"，不再强制展开。`cl.Step(type="run", default_open=False)`，前端 Radix Accordion 行为：默认 `data-state="closed"`，点击折叠头切到 `open` 展开完整过程活动。
- **折叠栏下方最近 2 条活动摘要**：每次工具调用时 `cl.Message(content="<marker>**{verb}** {summary}", author="FloodMind")` 发送一条 15 字摘要（超长截断 + …）；超出 2 条的标记为 stale 由 JS 隐藏。content 嵌入 `` `<span data-fm-act="N"></span>` `` 反引号代码块（markdown 不会解析内部 HTML），前端用 `textContent.indexOf('data-fm-act=')` 识别 + JS 把含 sentinel 的 text node 包成透明 span 隐藏。
- **点击展开时整组摘要隐藏**：JS 监听折叠头 button 的 `data-state` 变化（Radix Accordion 状态），展开时 `.fm-activity` 类 `display:none`，收起时恢复显示（stale 始终隐藏）。这样用户展开折叠栏看完整过程时，摘要不重复占用空间。
- **运行中过程中折叠头**：turn container `default_open=False`——Chainlit 不再强行打开 Accordion。折叠头始终显示"正在工作"（运行中）或"已完成 · X · 1 次工具调用 · k tokens"（完成后），用户主动点击展开。
- **任务完成后活动摘要自动消失**：`llm_step_end(reason=stop)` 时后端额外发一条 `cl.Message(content="\`fm-final\`")` sentinel，前端识别后整组 `.fm-activity` 永久隐藏（包括 sentinel 自身）。这样最终回答呈现时只有折叠头 + 最终回答两件事，干净清爽。

## [2.1.8] - 2026-08-30

> 折叠屏关键 bug 修复：子代理事件污染 + 主体被默认折叠 + 容器渲染策略。

### Fixed

- **子代理叙述回流污染主代理 UI**：`StepEventBus` 让子代理（SubAgent/ParallelTask 内）的 `answer_delta` / `thought_delta` / `llm_step_end` 全部回流到主 agent 的 event_bus 队列。Chainlit 端把所有带 `step_key` 字段的事件视为子代理事件并 `continue` 跳过（仅累加 token 到 turn 统计）。子代理结果通过 ParallelTask 工具步骤的 `action_end` 一次性呈现（`action_end` 不带 `step_key` 标识，路径是子代理整体完成上报的工具事件而非子代理单步 LLM 事件）。
- **折叠屏主体被默认折叠、用户无法展开**：`cl.Step(type="run", default_open=False)` 在 Chainlit 里渲染为 Radix Accordion 折叠步骤，且我之前用 JS 拦截了 click 阻止切换——结果折叠头显示但内容永远不可见。改为 `default_open=True`，配合 JS 拦截 click 即可做到"始终展开 + 不可点击"的 Codex 式屏幕效果。
- **in_turn_narr 容器在嵌套场景下 `default_open` 失效**：嵌套 Radix Accordion 子容器的 `defaultOpen` 行为不可靠，叙述 step 折叠头显示但内容藏起来。改为 `cl.Message(parent_id=turn.id)`：Message 没有折叠头，作为 turn 容器的子级渲染时内容始终可见，符合"折叠屏内所有内容平铺展开"的设计意图。
- **HTML 错误页直接糊在 chat 里**：DeepSeek 平台风控触发 429 时把 `<!DOCTYPE html><title>Error - Request Blocked</title>` 当成普通 content 抛上来。前端友好的做法是识别 `<!DOCTYPE` / `<html` 标记后换成"LLM 端点返回了非 JSON 错误（疑似触发平台风控/限流），请稍后重试或切换 provider"。

## [2.1.7] - 2026-08-30

> 品牌标识位置调整 + author avatar 隐藏：把 FloodMind logo 放到与折叠栏齐平的顶栏左侧，顶层消息 author avatar 不再显示。

### Changed

- **聊天主区顶部 brand slot 注入**：Chainlit 在 `<main>` 顶部 `<div id="header">` 内预留了一个空的 `.flex.items-center` brand slot（原本无内容），JS 现在把 FloodMind logo + 名称注入到该 slot（`.fm-header-brand`），位置在顶栏**最左侧**，与下方消息/折叠栏左缘齐平。语义上是 Chainlit 自己的 brand bar，UI 风格一致。
- **顶层消息 author avatar 隐藏**：每条 FloodMind 顶层 `cl.Message` 左侧原本会渲染 author 的字母 logo（`Author for FloodMind`），现在通过 JS 识别 `.ai-message` 第一个 avatar 子元素（`role=img` 或含 Radix collection item）后 `display:none`，并把 `.ai-message` 的 `gap` 归零，让消息内容贴左对齐。**品牌标识统一在顶栏 + 侧栏呈现**，消息栏不再出现冗余字母 logo。
- **`_close_in_turn_narr` 返回叙述文本**：`llm_step_end(reason=stop)` 路径在关闭 in_turn_narr 容器内叙述 step 后才能拿到完整文本，函数签名改为返回 `str`，避免叙述在容器关闭后丢失。

## [2.1.6] - 2026-08-29

> 折叠逻辑重构：折叠行变为不可点击的"屏幕"，所有过程活动与最终答复严格分离。

### Changed

- **折叠行变为不可点击的"屏幕"**：用户要求"折叠框就是一个屏幕，里面的内容按顺序展示"，但 Chainlit 的 mKn 折叠头默认可点击切换展开。改为：（1）CSS 让折叠头 cursor:default、关闭 hover 反馈；（2）JS 拦截折叠头 click/keydown，preventDefault + stopPropagation 让 Radix Accordion state 不被切换。容器始终保持展开状态，所有过程活动（思考/工具/中间叙述）按顺序流式累加显示。
- **中间叙述严格收到容器内**：此前中间叙述（如"好的，我立即并行启动两个子代理..."）在 turn 容器创建前已流式到顶层 answer（独立顶层 message），无法移入容器。重构后 turn 容器一旦创建，全部 `answer_delta` 写入容器的子 step (`in_turn_narr`)，不再独立顶层；turn 容器未创建时（纯问答场景）维持原行为流式到顶层 answer。
- **最终答复识别更精准**：原依赖 turn 容器中累积的叙述输出判断最终答复，现在综合三个来源——`answer` 顶层流（容器未创建时）、`in_turn_narr` 容器内叙述（容器已创建时），按 `llm_step_end(reason=stop)` 触发。避免容器创建前后行为不一致。
- **思考与叙述互斥**：同一容器内思考与叙述不能同时存在（它们都是 run-类型子 step）。新事件到来时关闭上一个：思考 → 关闭叙述、工具 → 关闭叙述、叙述 → 关闭思考。

## [2.1.5] - 2026-08-29

> 回合折叠 UI 微调与右上角冗余按钮清理。

### Changed

- **移除右上角 "说明" 按钮（README 弹窗）**：与品牌定位无关，且本服务无 README 文档。通过 `floodmind-ui.js` 直接 `#readme-button` 设置 `display:none; visibility:hidden;`。
- **welcome 页 logo 缩小 + 加副标题**：原 200px 偏大，覆盖为 96px；logo 下方追加 "FloodMind · 您的智能水文助手" 副标题（floodmind-ui.js 注入 `.fm-tagline`）。
- **侧栏顶部品牌行**：侧栏 logo 由 150px 缩到 28px（与新建会话图标同高），并追加 "FloodMind" 产品名（floodmind-ui.js 注入 `.fm-brand`）。
- **叙述段折叠行不再显示首行标题**：模型在工具之间的过程叙述（如"输出为："）以前会被 mKn 截首行作为折叠标题，造成冗余"输出为："折叠行。叙述段 `cl.Step(name="")` 后不再渲染标题，仅展开填充内容。
- **品牌资源部署链路修复**：发现 `<workspace>/public/floodmind-ui.{js,css}` 与随包源文件不同步会导致修改不生效。手动同步已修复；后续品牌定制需同时改源包并 cp 到 `<workspace>/public/`（install 流程启动时会自动覆盖回源包最新版）。

## [2.1.4] - 2026-08-29

> 2.1.3 修复后用户实测暴露的委派路径体验问题（首轮只说不做、开局失败行、过程自白混排）。

### Fixed

- **主 agent 首轮"只说不做"**：实测模型第一轮只输出"好的，并行启动两个子代理…"便结束回合，用户不得不重发同一句话。双重修复——WORK_METHOD_GUIDANCE 增加"已决定的操作直接调用工具执行，不要输出准备性叙述后停止"；同时修正该段工具名漂移（"用 Task 委派"→SubAgent，Task 工具并不存在）。
- **开局"✗ 调用 SubAgent · 0.0s"失败行**：委派工具在 progressive 模式下需先 GetTool 加载，模型首轮直调必被拒且 UI 留下失败行。orchestrator 的 core_tools 预载 SubAgent/ParallelTask（registry 缺名自动过滤，bare 模式不受影响）。
- **过程自白与最终答案混排**：所有 answer_delta 拼进同一条消息，"工具尚未加载，先获取工具参数"之类的过程自白与最终汇总连成一坨。改为工具调用发生时切断回答流（Codex 式叙述段——短叙述独立成消息，与工具折叠行交替出现）。
- **委派工具行动词与结果**：_TOOL_VERBS 补 SubAgent→"委派子代理"、ParallelTask→"并行委派"（此前显示"调用 SubAgent"，另有无用的 Spawn 映射已删）；委派结果不再 JSON dump，解析提取 status/任务/summary/产物为可读文本。
- Chainlit UI 真实浏览器端到端复验：首轮即"并行委派 · 9.6s"折叠行（无失败行），叙述段独立，结果正确。

## [2.1.3] - 2026-08-29

> 修复子代理（SubAgent/ParallelTask）委派后无法执行任何实际操作的缺陷——用户实测"启动两个子代理正反向拼写"暴露。

### Fixed

- **子代理工具集被误清空（根因）**：`_is_specialist_builtin_safe` 按 `is_destructive` 一刀切过滤，而 Bash/Write/Edit 均标记 `is_destructive=True`，导致 specialist 注册表只剩 GetTool/GetSkill 元工具——子代理调用 Bash 收到"当前未加载，不能执行"，最终只能放弃并自行编造结果（与设计注释"Specialists execute scoped work, including file edits and shell commands"直接矛盾）。改为排除 agent 全局状态与管理类工具（state_write 策略 + MemorySearch/ConversationSearch/UpdateProjectInstructions/TaskKill 名单），执行类工具交给每次调用的权限策略兜底（exec/write 路径校验、危险命令硬拒、子代理 ASK 自动降级 DENY，均已实测）。
- **子代理系统提示词缺工具目录**：SPECIALIST_STATIC_GLOBAL 只注入 skill_catalog，progressive 模式下子代理看不到"有哪些工具、如何加载"，只能盲猜工具名（实测幻觉出 `Agent`）。补上 `## 可用工具` 目录段（full 模式初始构建与 `_rebuild_system_prompts` 重建、bare 模式均覆盖）。
- **specialist 渐进加载预载核心工具**：specialist 的 core_tools 追加 Bash/Read/Write/Edit/Glob/Grep（不占 max_loaded_tools 配额，registry 不存在的名字自动过滤）——子代理生命周期短，此前每次委派都要先绕 1-2 轮"未加载→GetTool→重调"。实测修复后子代理首轮 Bash 直接执行成功（4/4，修复前先失败 4 次才走上正轨）。
- **child_thread.result Journal 双写**：子线程与父线程 JournalAuthority 共享同一份 run journal（同 runtime_dir/run_id，thread_id 作用域在此处本就相同），终态事件被连续 append 两条内容完全相同的事件；且与 accepted/running 及异常路径（仅 parent_auth.emit）不一致。去重为单次记录。
- **checkpoint 保存的并发一致性（压力测试确证）**：并行 specialist 共享同一份 run journal，`_save_checkpoint` 先 `cursor()` 再 `replay()` 的两次独立读取之间可能被并发 append 插队，触发"RunState snapshot cursor 与 checkpoint journal_cursor 不一致"校验失败（checkpoint 静默跳存）。新增 `JournalAuthority.checkpoint_snapshot()` 原子快照（单次 read_from，cursor 取批内最后一条 sequence，偏旧安全、偏新有害），并发压力测试从 130 次错位降为 0。
- 同步更新 `test_full_specialist_excludes_state_write_and_destructive_tools`、`test_agent_accepts_tool_loading_config`（此前固化了误清空行为）；全量测试 1188 通过。

## [2.1.2] - 2026-08-29

> 采纳开源 Chainlit（github.com/Chainlit/chainlit，50k+ star）作为 gateway 前端，并补齐历史会话持久化；思考/工具展示对齐 Codex 桌面端风格（经用户确认的调研结论）。

### Added

- **Codex 风格过程展示**：调研 Codex 桌面端（官方截图 + 逐字 UI 字符串交叉验证）后对齐——工具行动词化（"运行/读取/写入/搜索/抓取 `对象`"）+ 成败标记（✗）+ 耗时（·0.4s），运行态"使用中"前缀；思考流折叠为"思考了 N 秒"；每轮状态行（TaskList）结束回填"已完成/失败 · N 秒"；审批改为普通消息卡片 + 允许/拒绝按钮（Codex 式内联审批）。
- **审批闭环修复（Chainlit 环境缺陷绕开）**：
  - **AskActionMessage 弃用**：其 socket call/ack 机制在本环境前端不渲染（stub 步骤未上树、无 ack），90s 超时被静默当作"拒绝"（`timeout=90` 默认值），实测复现并经服务端 sio.call 日志确证。改为普通消息 + `cl.Action` + `@cl.action_callback` 回调，走与工具行相同的 new_message 渲染链路；
  - **stub 步骤挂载陷阱**：打开中的 Step 首次 `stream_token` 会向 `local_steps` 压入一条前端不渲染的 stub 消息，其后发送的消息（含审批卡）默认挂在它下面被前端整棵丢弃——发审批卡前显式清空 `local_steps` 使卡片挂根；
  - **运行中按钮禁用**：前端在 loading 态禁用全部 Action 按钮（bundle 实证 `disabled: loading`）——审批卡发出后 `task_end()` 启用按钮，批准回调里 `task_start()` 恢复运行态。
- **Chainlit UI（`floodmind gateway --ui chainlit`）**：`floodmind/gateway/chainlit_app.py` 把 Agent SDK 流式事件桥接到 Chainlit——answer_delta → 流式 markdown、thought_delta → 思考折叠、action_* → 工具面板、permission_ask → 审批卡（AskService 闭环续跑）、产物 → File/Image 元素；CLI 以子进程拉起 `chainlit run`（`[chainlit-ui]` extra：chainlit + sqlalchemy + aiosqlite）。
- **历史会话持久化（`floodmind/gateway/chainlit_history.py`）**：
  - SQLite 数据层（SQLAlchemyDataLayer，落 `<workspace>/.floodmind/chainlit/threads.db`）：内置建表与旧表 ALTER 迁移（数据层不自动建表；StepDict 列集如 defaultOpen/autoCollapse 需显式覆盖）；
  - thread → FloodMind session 确定性映射 + metadata 双保险，`on_chat_resume` 恢复历史会话后续聊接续同一份 Journal（实测：恢复后 agent 能复述会话开头的 session 编号）；
  - 首条用户消息自动命名会话，恢复后不重命名；
  - LocalStorageClient：产物文件落盘 `.floodmind/chainlit/files/`，注册 `/floodmind-files/*` 路由（插到 SPA 兜底路由之前）回源，历史中的产物卡片重启后仍可打开；
  - 本地无感鉴权补丁：Chainlit 历史侧栏仅在 `requireLogin && dataPersistence` 时渲染、`/project/threads` 未鉴权 401、websocket 握手无 cookie 直接拒绝——将 auth 解析补丁为固定本地用户（对齐 gateway 回环免鉴权惯例），浏览器零交互。
- **FloodMind 品牌化与 UI 增强**：名称/logo/favicon/消息头像全部替换为 FloodMind 品牌（复用 `web/public/floodmind-icon.svg`，主题色 #0ea5e9/#38bdf8，随包分发于 `gateway/chainlit_public/`，启动时落到 `<workspace>/public/`）；`config.ui.name="FloodMind"`；CLI 启动 Chainlit 子进程固定 `cwd` 与 `CHAINLIT_APP_ROOT` 到工作区（不再随 shell cwd 漂移）；custom_js 给原生新建会话图标按钮补 title/aria-label；服务重启导致旧页面跳 /login 时自动送回主页（sessionStorage 防循环，本地模式无登录表单）。
- **"新建会话"确认弹窗文案修正**：默认文案"这将清除您当前的聊天记录"与实际行为不符（有数据层时旧会话完整存入侧栏历史并可恢复，实测验证）——包装 `load_translation`（pydantic 类级补丁）按语言替换为准确描述，不改 Chainlit 包文件。
- **新会话回归原生 welcome 空页**：移除 on_chat_start 的 greeting 消息（会话/模型信息按需向 agent 询问），新建会话即见品牌 logo 居中的空状态页。
- **Codex 式降噪**：GetTool/ListTools 等内部加载步骤不再渲染（此前每轮多出 2-3 条"加载工具"行）；工具行/思考行显式收起（default_open=False）。
- 新增回归：Chainlit UI 端到端手工验证（新会话 → 侧栏列表 → 刷新持久 → 点击恢复完整消息 → 续聊 session 连续；审批卡显示 → 允许 → executor 续跑 → 文件写入/读取 → 状态回填；品牌 logo/标题/头像；新建会话按钮；降噪后的工具行）。

## [2.1.1] - 2026-08-29

> 第二轮对抗性审查：对第一轮改动本身（发现 4 个 P1 回归）、尚未覆盖的记忆/上下文/Skill 域（1 个 P0 + 17 项）、工具实现/MCP/Provider 域（44 项，其中 2 项实验复现）做全量复查并修复。

### Fixed（第一轮改动引入的回归）

- **journal 撕裂尾上追加致事件静默丢失（P1-1）**：append 以 "a" 模式写在物理 EOF，坏尾未处理时新事件落在坏行之后（本次可见、重启即丢、序列复用、哈希链断链）。现 append/append_many 前主动探测并安全截断撕裂尾（中部损坏仍拒修 fail-closed）。
- **exec 预授权与执行层自相矛盾（P1-2）**：host_preapproved / authorized_ask_id 放行的 exec 调用未登记"未解析写目标"批准，执行层消费不到批准而拒绝——always-trust 模式与网关批准路径最该跑通的场景反而失败。现登记统一收敛到 ToolExecutionService 的 ALLOW 分支（三条放行路径全覆盖）。
- **stream 串行锁竞态（P1-3）**：消费端提前关闭后 generator finally 立即释放锁，worker 仍在收尾，新 stream() 可与旧 worker 并存（复现 D-02）。现锁释放权归 worker（含归属守卫 `_current_run_context`），generator 仅兜底线程未启动场景。
- **调度器跨进程重复执行（P1-4）**：claim 现记录 `claimed_by`（pid@host），recover 前探测进程存活（psutil），存活者仅超硬上限（2×阈值）才回收；一次性任务恢复后顺延 5 分钟防 fire 循环。
- 工具池：线程启动失败归还信号量槽位（防静默泄漏）；饱和语义恢复"排队等待 10s"（等效旧 8+32 缓冲，并行委派不再轻易饱和）；删除不可达的 cancelled_before_start 死分支。
- checkpoint：路径校验提前到 state 变更之前（校验失败不再残留脏值）。
- StepEventBus：set_queue/listener/persist 槽位方法全部委托 parent，消除"静默操作死槽位"。
- Gateway：SSE 生成器 finally 不再 yield（GeneratorExit 后 yield 抛 RuntimeError）；同会话新 run 先中止旧 run；DELETE 会话先 abort；session_id 校验失败返回 400；token 常量时间比较、空 token 拒绝启动。
- 长期记忆：add 路径写前 reload-merge，多 Agent 实例不再 last-writer-wins 互相覆盖。
- PROJECT_ROOT：site-packages 安装形态回退 `~/.floodmind`（D02 真正闭环）；settings.json / mcp.json 保存改原子写；gateway 不再配置永不生效的清理线程；临时脚本清理 + e2e 去 token 硬编码。

### Fixed（记忆/上下文/Skill 域）

- **上下文压缩静默丢消息（P0）**：head 边界落在 assistant(tool_calls) 原子组上时，整组被三处（head/摘要/tail）同时排除。改为窗口交集语义：与压缩窗口有任何重叠的原子组整组纳入摘要源。
- **压缩触发震荡（P1）**：need_compress 此前用上次返回文本估算，稳态"压缩/全量"交替、压缩形同虚设；改基于本次全量文本。
- **bind_journal 不清压缩缓存（P1）**：重绑会话后旧摘要跨会话泄漏进 system prompt；bind 时清缓存 + cache key 掺 conversation_id。
- context_window 回退链删除 maxTokens 兜底（生成上限不再冒充记忆窗口）+ 数值钳制；经验树/索引 JSON 损坏先隔离 .corrupt 文件再降级（不再被空数据覆盖）；curator 归档/恢复同名冲突报错（不再 rmtree）；CreateSkill frontmatter 改 yaml.safe_dump（防注入）；Skill 路径校验补 Windows junction（reparse point）检查；经验摘要按 6000 字符预算裁剪；渐进收紧实现真三轮（2000→1000→500）；context_runtime 跳块加日志。

### Fixed（工具实现/MCP/Provider 域）

- **exec_bash 超时杀进程树（P0 级资源泄漏）**：主代理超时只 kill 直接子进程，孙进程全泄漏且无兜底；统一 taskkill /T /F（POSIX killpg）。
- **文件工具数据损坏三连（实测复现）**：GBK 回退写回可截断文件为 0 字节（先试编码再写）；LF 文件被改成 CRLF（bytes 直写零翻译）；ApplyPatch 把非 UTF-8 文件乱码化（无法无损解码即拒绝）；ApplyPatch 空行丢弃致 hunk 错位（保留原始行 + 上下文校验 fail-closed）；Write/Edit/ApplyPatch 全部原子写。
- **中文 Windows 输出乱码（P1）**：exec_bash 强制 UTF-8 解码 powershell GBK 输出；改 UTF-8→GBK 探测解码；输出 64KB 封顶截断；timeout 钳制 [1,240]；WebFetch 对无 charset 中文页改用 apparent_encoding。
- **MCP stdio 断开杀进程树（P1）**：terminate 只杀直接子进程，node 孙进程泄漏。
- **子代理 token 配额失效（P1）**：从 usage 事件的 content JSON 取值（原读恒空的 raw）。
- 杂项：codec tool_call index=None 不再崩流；非流式 chat 空 choices 返回中文错误事件；子代理复用父 StepEventBus 的 _trace_session_id 用后复原。

### Removed

- 删除未接线死模块：`tool_guardrails.py`（356 行，防循环由 executor doom_loop_tracker 承担）、`error_classifier.py`（330 行）、`model_router.py`（178 行，SMART_TIMEOUTS 引用不存在的工具名）及对应测试文件。

### Verification

- 完整回归：**1188 passed, 6 skipped**（新增：journal 撕裂尾追加、调度器 7 项、文件工具 12 项、GBK 解码、压缩窗口覆盖、junction、frontmatter 等约 45 个新测试；删除死模块测试）。

## [2.1.0] - 2026-08-29

> 本版本由对抗性审查驱动：以 Claude Code（hooks/permission 决策模型）、OpenAI Agents SDK（guardrails + human-in-the-loop）、LangGraph（checkpointer/thread/interrupt）、OpenHands（事件溯源/增量持久化）的公开工程实践为硬标准，对 Agent runtime 的鉴权、钩子、会话管理三域做全量审查（鉴权 13 项 / 会话 22 项 / 钩子 17 项缺陷），按 P0→P2 分波修复。

### Added

- **Gateway 网关 + Web UI（`floodmind gateway`）**：把 SDK Runtime 暴露为 HTTP 服务（`floodmind/gateway/`）。
  - 鉴权默认策略对齐本地工具惯例（Ollama/ComfyUI/Open WebUI 单用户模式）：**回环地址默认免鉴权，启动即拉浏览器直进 Web 界面**；非回环地址（局域网）自动开启 token 并采用 Jupyter 式一键进入（token 自动拼入打开的 URL，前端收下后从地址栏抹掉）；`--auth`/`--token`/`--no-auth` 显式覆盖；
  - Web UI 全量重写为 Codex 风格（近黑单色、扁平无气泡、mono 工具行）：会话侧栏（过滤/删除/自动标题）、流式 markdown（代码块复制）、Thinking 折叠块、工具调用行（状态点/耗时/输入输出展开）、计划进度卡片、权限审批卡片（键盘 Y/N）、产物卡片（图片内联预览/下载，新增 `GET /api/file` 会话内文件服务）、错误块、token 计数、空状态建议提示词、Esc 停止；
  - 会话自动命名：首条用户消息截断作为标题（"Untitled"/"新会话" 视为未命名）；
  - 端点：会话列表/创建/删除/历史投影（v2 canonical journal 投影）、`POST /api/chat` SSE 流（answer/thought/tool/permission_ask 原样转发）、`POST /api/chat/abort`（run 级取消）、`POST /api/permission/respond`（非阻塞 ASK 闭环）、`GET /api/file`、`/api/health`；
  - CLI：`floodmind gateway --host --port --workspace --token --auth --no-auth --no-open`；pyproject 新增 `[gateway]` extra（fastapi+uvicorn）。
- **共享跨进程文件锁 `floodmind/common/filelock.py`**：Windows msvcrt（循环重试至超时）+ POSIX flock 统一封装，`FileLockTimeoutError` 语义明确。
- **定时任务调度循环**：`ScheduledTaskRuntime.start_scheduler(execute_fn)` / `stop_scheduler()` / `recover_stale_running()`——此前 `claim_due_tasks` 全仓无调用方，任务创建后永不执行（P0）；僵尸 running 状态（崩溃残留）自动重置 pending。
- 新增回归：`tests/test_journal_writer.py` 撕裂尾 2 项；`tests/test_permission_host_fixes.py` 预授权门语义 4 项。

### Fixed（安全/权限，P0-P1）

- **宿主 `permission_handler` 不再越过 SDK 安全层（原 P0）**：`True` 语义从"直接 ALLOW 跳过全部检查"降级为"宿主预授权"——可满足策略级 ASK（桌面 always-trust），但子代理 tier、planning 硬门、路径校验、危险命令、全局 deny 规则照常生效；与 `permission_decision_hook`（只能收紧）语义对齐。
- **全局 deny 规则先于 ASK（F-05）**：用户批准不再能翻越宿主显式拒绝规则。
- **`skill_script` 纳入 planning 硬门；缺参 fail-closed DENY（F-11）**。
- **长期记忆静默丢失（D02，P0）**：`LongTermMemory` 从安装包目录迁至 `PROJECT_ROOT/data/memory/`（site-packages 只读安装下写入失败即全部丢失）；tmp+fsync+os.replace 原子写；进程级锁；读路径不再整文件重写；首次运行自动迁移旧文件。
- **CheckpointService 路径穿越（D01，P0）**：`session_id`/`checkpoint_id` 白名单校验（字母表 + Windows 保留名 + `ckpt-` 前缀 + containment 断言）。
- **默认根收敛（D19）**：CheckpointService 默认 `PROJECT_ROOT/data/sessions`，不再依赖 cwd。
- **checkpoint 链断链（D08）**：save 失败回滚 `state.checkpoint_id`。

### Fixed（持久化健壮性）

- **多字节撕裂尾令 journal 整体不可读（D05，P1）**：segment 读路径改二进制逐行解码（errors=replace），坏行只影响自身；append 失败补 repair_tail 恢复契约。
- **repair_tail 可误删合法事件（D04，P0）**：只允许截断"最后一个完整合法行之后直到 EOF"的撕裂尾；中部损坏抛 `JournalMidFileCorruption` 拒绝截断。
- **坏行读/写语义统一（D14）**：read_from 与 reconcile 一致按"坏行即段尾"。
- **journal 跨进程锁统一走 FileLock**：Windows LK_LOCK ~10 秒抛裸 OSError → 循环重试至 30s 超时。
- **resume lease TOCTOU + 误删（D06 子集）**：open_lease 的 exists→read→write 在 FileLock 内原子完成；lease 携带 owner token，release 只删除自己的 lease。
- **scheduled_tasks.json**：tmp+fsync+os.replace 原子写；跨进程 FileLock 保护 read-modify-write。

### Fixed（运行时健壮性）

- **共享工具池卡死瘫痪面（D-01，P1）**：固定 8 worker 共享队列（卡死即永久占位，8 个卡死全进程工具执行瘫痪）改为"每调用一线程 + 信号量限流"；超时遗弃线程立即归还并发额度（exactly-once 归还），饱和错误携带 in_flight/max_concurrency 诊断。
- **同一 Agent 并发 stream() 守卫（D-02，P1）**：非阻塞 `_stream_lock` 探测，冲突显式 RuntimeError（此前 queue/journal/memory 绑定互相覆盖、事件串台）。
- **abort_check 异常不再炸穿 run 循环（D-05）**：统一包装按"未中止"处理；消费端提前关闭（GeneratorExit）触发 run 级取消（D-13）。
- **async 宿主回调显式拒绝（D-09）**：on_event / permission_handler / permission_decision_hook / abort_check 传 async 函数时构造期 TypeError（此前协程被静默丢弃或当"无意见"）。
- **幂等键单一来源（D-04）**：executor 透传的 idempotency_key 不再被 service 本地二次派生遮蔽。
- **StepEventBus 继承重构（D-11）**：删除逐字复制的 19 个 emit 方法，只覆写 emit()。
- **background_review 接线（D-10 子集）**：`spawn_background_review` 受 `settings.background_review` 开关控制并接入 stream 收尾（偏好→长期记忆、经验→经验树、Skill 建议→待审核队列）。
- **SDK 构造不再依赖全局配置**：`Agent(llm=...)` 在 `~/.floodmind/settings.json` 无 providers 时以默认记忆窗口（32768）降级构造并告警，不再抛错（此问题曾导致干净机器上 70 个测试失败）。

### Changed

- `Agent(permission_handler=True)` 语义变更见上；README/接口文档同步更新。
- 测试基线：**1167 passed, 6 skipped**（含新增撕裂尾/预授权门回归）。

### Known issues（后续批次）

- journal 每次追加重扫全量 segment、`_sealed` 无界缓存（长会话 O(N²) I/O，D13）；
- resume 全量 replay 多次重复（executor 每 checkpoint 一次全量重放 + 全量序列化）；
- 未接线死组件待清理：tool_guardrails / error_classifier / model_router / 全局 ToolRegistry（约 1200 行，均有单测引用，删除需同步清测试）；
- plugin 钩子无卸载路径、cwd 相对插件目录的任意代码执行面（D-07/D-08）；
- 事件类型白名单与实际发射漂移（D-12）。

## [2.0.4] - 2026-08-13

### Fixed

- **系统提示词引导冲突：文档编辑类 skill 被"写脚本"默认引导压制。** 系统级引导此前给出绝对化指令——主代理 `Write + Bash 执行非 Python 脚本`、子代理 `编写并执行临时 Python 脚本` / `优先使用 Bash 执行 skill 中的脚本`——压过了 docx/pptx/xlsx 等 skill 正文的 `Use the Edit tool directly. Do not write Python scripts`，导致 MiniMax-M3 倾向写脚本而非用 Edit。现统一确立优先级：**skill 正文的执行方式说明优先于系统默认引导**。
  - 主代理 `TOOL_EXECUTION_GUIDANCE`：`Write + Bash` 从绝对指令改为"手段之一"，明确正文指定执行方式（docx/pptx/xlsx 用 Edit 直接编辑）时以正文为准；
  - 子代理 `SPECIALIST_STATIC_GLOBAL`：临时脚本限定为"数据处理/分析类任务"，新增"skill 正文优先"执行原则，`Write + Bash` 同改为"手段之一"；
  - 子代理用户输入注入：`优先使用 Bash 执行 skill 脚本` 改为 `遵循 skill 正文指定的执行方式`（正文要求 Edit 就用 Edit、不写脚本；正文提供脚本才用 Bash）。

### Verification

- 完整回归：**1166 passed, 1 skipped**（clean checkout）。

## [2.0.3] - 2026-08-12

### Fixed

- **create_plan / update_plan 工具契约（desktop 实验 2 报告，三处 schema 与实现不一致）：**
  - `expected_deliverables` 每项接受字符串或对象（此前 schema 只收对象、实现却归一化字符串 → 模型传字符串被 jsonschema 拒 `is not of type object`）；
  - 步骤 `status` 枚举并入 `in_progress`（子任务习惯状态），handler 归一化为 `running`，两套枚举不再打架；
  - 多余参数键不再崩 `_handle_create_plan/update_plan`：handler 接受 `**kwargs`，且 `create_plan`/`update_plan`/子任务 schema 均加 `additionalProperties:false`（多余键被 jsonschema 干净拒绝，create_plan 步骤 schema 补全实现读取的全部字段避免误拒）。
  - 回归测试：`tests/test_plan_contract.py` 4 项。

### Verification

- 完整回归：**1166 passed, 1 skipped**（clean checkout）。

## [2.0.2] - 2026-08-12

### Fixed

- **Agent 尊重 ModelClient 的 `enable_thinking`（"模型思考中" tag 修复）**：`NativeFloodAgent.stream(enable_reasoning=False)` 默认值此前会强制把 `model_client.enable_thinking` 覆盖为 False，即使宿主注入的 ModelClient 自带 `enable_thinking=True` —— 请求带 `thinking:disabled`，模型不流式推理，前端收不到 `thought_delta`。现 `enable_reasoning` 默认 `None`，仅显式传 True/False 时覆盖；`None` 尊重 ModelClient 自身设置。
  - 真实 MiniMax API 验证：直连与带 tools 的 `stream_chat` 均正常流式推理（29~215 个 reasoning 事件）；修复后 agent 路径稳定产出 `thought_delta`（实测 146 个）。
  - 回归测试：`test_agent_preserves_modelclient_enable_thinking`（捕获请求断言 `thinking:adaptive`，修复前必失败）。

### Verification

- 完整回归：**1162 passed, 1 skipped**（clean checkout）。

## [2.0.1] - 2026-08-12

### Added

- **公开 `wait` / `recover` / `resume` 结构化流事件**（§10.1/§4.4，desktop/LS 契约）——补齐前端可靠区分"重试前退避等待 / 重试后恢复 / checkpoint 恢复"的能力：
  - `wait`：LLM 重试前的退避等待 `{"type":"wait","reason":"retry_backoff","attempt":N,"duration":seconds}`（executor 在 `sleep(delay)` 前发出）；
  - `recover`：重试成功后恢复 `{"type":"recover","attempt":N}`；
  - `resume`：`Agent.resume(checkpoint_id, ...)` 在 orchestrator event_bus 上发出 `{"type":"resume","checkpoint_id":"...","status":"started|completed"}`，与 Journal 的 `resume.started/completed` 对齐。
- **后台任务宿主契约**（desktop Phase-1 反馈，SDK 侧补齐）：
  - `Agent.list_background_tasks()` / `Agent.kill_background_task(task_id)` 公共 API —— 宿主不再需要 `agent.raw._background_task_service` 私有字段壳；
  - 完成通知不再消费查询集：`drain_completions` 只注入一次，完成的任务 `list()/get()` 仍可查（桌面面板不消失）；
  - 重启对账回填：`reconcile_background` 把 orphaned/unknown 写 meta 的同时回填 `_completed` 查询集；
  - `list()/get()` 覆盖完整声明的状态集（含 starting 与对账后的 orphaned/unknown）；
  - `bind_workspace` 重设后台任务存储根（任务不再落旧工作区）；
  - 日志尾部语义：stdout+stderr 合并尾部（stderr-only 失败可见）、`TaskOutput` 改有界尾部读（大文件不整读）。
- `Agent.stream()` docstring 事件契约同步更新，作为宿主（LS Agent 等）薄适配层的文档化依据。

### Verification

- 完整回归：**1161 passed, 1 skipped**（clean checkout）。

## [2.0.0] - 2026-08-11

> 重大版本：按 `FM_ARCHITECTURE_BASELINE.md` 完成 **forward-only 架构迁移（P0–P8）**。不向后兼容、不 fallback、不保留 legacy adapter，直接落 TARGET 契约。

### Added（TARGET 架构交付）

- **Canonical Event Journal + Deterministic Reducer**：JSONL Journal 为唯一运行事实源；确定性 `reduce(state, event)` 派生状态；`_turns` 变为 Reducer 派生投影，旧 `chat_history.json` 历史源下线且无读取回退。
- **身份层级**：`conversation_id / task_id / run_id / thread_id / turn_id / attempt_id / call_id / transaction_id / artifact_id / checkpoint_id`（§3.1）。
- **Tool Transaction + Approval Fingerprint + 幂等**：`proposed→…→succeeded/failed/denied/cancelled/indeterminate` 终态机，Pending Reconciliation，Checkpoint Resume（Journal Replay + Reconciliation）。
- **Model Layer 四层**：`ModelTransport → ProviderCodec → ResponsePipeline → ModelCapabilities`；Provider 原生块无损保存。
- **Context / Memory**：Projection Manifest、输入预算、Journal-backed Compact（Atomic Groups + Summary Event）、Soul/Core/AGENTS 版本化 + Provenance。
- **Sandbox / Artifact / Background**：`SandboxBackend`（OS/Container 边界，Landlock fail-closed）、`ArtifactService`（内容寻址 + 原子发布）、Background Kill 验证链 + Restart PID Reconcile。
- **ChildThread Runtime**：`ChildThreadRuntime` 替换 ad-hoc Specialist —— quota（max_turns/max_tokens/wall_clock）、Typed `SubagentResult`、父取消树（验证式清理）、child background namespace、严格子集权限隔离、SandboxBackend 会话绑定。
- **标准 SDK 公共 API**：`Agent` 标准身份 + `events_after(sequence)`（Journal 派生 committed 事件，可重放/对账）+ `resume(checkpoint_id)`（ResumeService 真实路径）。
- **SQLite 派生 Journal 索引**：`SqliteJournalIndex` 可重建、count 完整性校验、跨线程安全；JSONL 仍为唯一权威（§18）。

### Removed

- **Web / TUI 前端**：`floodmind/server/`、`floodmind/tui/`、`web_server.py`、`start.py`、web 调度器与 web SSE 存储（`sync_events`）整体移除；CLI `web` / `serve` / `tui` 命令删除。
- **历史源 / legacy 适配层**：`chat_history.json` 读取回退、ContextVar 全局 getter 的 fallback、旧 `AgentTool→ToolSpec` 兼容、Shadow Journal 双写过渡。

### Verification

- v2.0.0 完整回归：**1154 passed, 1 skipped**（唯一 skipped = Linux Landlock 平台测试，Windows 环境跳过）。

## [1.2.0] - 2026-08-08

### Added

- **宿主项目 Skill roots 公共 API**：`Agent(skill_roots=[...], skill_writable_root=...)` 支持宿主显式部署一个或多个 `SKILL.md` 根，并通过公开的 `agent.skill_registry` 检查实例目录与解析结果。顶层新增稳定导出 `SkillRegistry`、`SkillRoot`、`create_skill_registry`。
- **每 Agent Skill 运行时隔离**：每个 Agent 构造独立 `SkillRegistry` 与 `SkillCurator`；实例绑定的 `GetSkill` 缓存、Curator 使用统计、TaskExperience 与状态路径不再共享。bare/full 均提供 catalog + `GetSkill`，full 仅向 orchestrator 追加 CRUD，specialist 仍只有 `GetSkill`。

### Changed

- **发现优先级固定**：同名 Skill 按 `builtin > host > project > .claude > ephemeral` 选择；显式根在构造时规范化为绝对路径，后续切换 CWD 不改变含义。`workspace` 与 Skill roots 相互独立，运行时不会隐式扫描 workspace。
- **Skill 根默认只读**：发现根只加入运行时读授权，不会给普通 `Write` / `Edit` / `Bash` 增加写权限；只有 `skill_writable_root` 是 CRUD 写源。内置、只读根与 ephemeral Skill 不能 Update/Remove；CRUD 对 canonical path、symlink 与 containment 做约束检查。
- **全局 API 保持兼容**：`get_skill_registry()` / `register_skill()` 仍操作历史默认全局 Registry，并保留原状态路径与旧调用行为；Agent runtime 不再依赖该全局单例。
- **宿主集成边界**：LS_Agent 可把已部署的 `SKILL.md` 目录作为显式 `skill_roots` 传入 FloodMind，本仓库不修改 LS_Agent。

### Verification

- 版本与 CLI 定向测试已执行；v1.2.0 完整回归数量由发布主流程确认，不在此预填。

## [1.1.9] - 2026-08-06

> 注：无 v1.1.8——其内容（ContextCompressor 原子组 + context_window 跟随注入模型）经确认属 v1.1.7 的 MiniMax 2013 根因链，已并入 v1.1.7。

### Fixed

- **P0-1 `exec_bash` 子进程关闭 stdin（挂起主因）**：`_impl_exec_bash` 的 `Popen` 此前只设 stdout/stderr 管道，stdin 继承父进程。模型发出读标准输入的命令（裸 `python`、`python -`、交互式程序）时子进程永久等输入，直到 120s 默认超时才被 kill——实测一轮任务 Bash 挂起约 4 分钟，用户侧看就是"智能体没反应"。现设 `stdin=subprocess.DEVNULL`（一次性执行工具本就不支持交互输入）。
- **P0-2 Bash 工具描述告知 shell 类型 + stdin 已关闭**：描述只写"自动选择可用 shell"，Windows 上实际是 PowerShell，模型不知道就照写 bash 方言（`2>/dev/null`、heredoc）。新增 `_bash_shell_hint()` 动态带检测结果：`当前 shell：powershell（用 ; 连接、勿用 2>/dev/null、&&、heredoc）` + `stdin 已关闭，禁止交互式/读标准输入命令，Python 先写文件再执行`，接入 Bash 工具描述与 `ExecBashInput.command` 字段说明。
- **P1-1 完整模式注册宿主自定义 tools**：`tools` 参数此前只有 `_init_bare` 消费，完整路径 `_init_tools()` 只注册内置工具，宿主注入的业务工具被静默丢弃。现 `_init_tools()` 末尾（内置 + MCP 之后、`_init_executors` 快照 tools_schema 之前）补注册到 orchestrator 与 specialist 双 registry。
- **P1-2 完整模式保留宿主 system_prompt**：`system_prompt` 参数此前只在 `_init_bare` 使用，完整模式忽略。现 `__init__` 存 `_host_system_prompt`，`_init_executors` 与 `_rebuild_system_prompts` 都把它作为独立段注入——skill 热插拔重建提示词时宿主段不丢。
- **P2 未声明 permission_policy 回退 is_readonly**：`PermissionRequest` 新增 `is_readonly` 字段（ToolExecutionService 填充）。未显式声明 policy 的工具此前一刀切 DENY，宿主用 `build_agent_tool` 标了 `is_readonly=True` 仍被拒，接入成本高。现回退看 `is_readonly`：True 按只读放行，False 走 ASK/DENY。

### Added

- **后台任务（`Bash run_in_background=True`）**：长任务不再受同步 120s 超时限制，可异步跑完再由 Agent 感知。
  - `BackgroundTaskService`（`floodmind/agent/runtime/services/background_task_service.py`）：stdout/stderr 直写文件（无 PIPE 死锁风险），文件落 `.floodmind/sessions/<sid>/background/<task_id>/{out.log,err.log,meta.json}`；每任务 daemon wait 线程 → 完成队列 → subscribe 回调；Windows `taskkill /PID /T /F`、POSIX `killpg` 杀进程树。
  - `exec_bash` 新增 `run_in_background` 参数：走全部安全管线（危险命令/写目标/workdir/sandbox）后交服务托管，立即返回 `task_id` + 文件路径；同步路径零改动。显式 `timeout` 作为存活上限覆盖，默认 30 分钟兜底 kill。
  - 三个工具（完整 + bare 双模式注册）：`TaskOutput(task_id, tail_lines=200)` 只读查状态/输出尾部；`TaskList()` 只读列本会话任务；`TaskKill(task_id)` exec 策略杀进程树。
  - executor 完成通知注入：`_inject_background_notifications` 在每次 LLM 调用前 drain 本会话完成队列，以 user 角色消息（`[后台任务完成/失败] …`）追加 state.messages——与排队用户消息同通道，厂商兼容性最好。
  - 空闲唤醒：Agent 初始化订阅任务完成 → EventBus 发 `background_task_completed` 事件（运行中的 stream 带出，宿主 UI 实时可见）；宿主收到且无活跃回合时自行决定是否开新回合，SDK 不越权自发回合。`Agent.cleanup()` / `__del__` kill 本会话存活任务（meta.json 保留供审计）。
  - 护栏：单会话并发上限 8（可配置）、单任务最大存活 30 分钟兜底 kill、会话结束清理存活任务。

### Fixed

- **permission_handler 改为宿主最高裁决**：此前 `permission_handler` 返回 `True` 只表示"不拒绝"，SDK 的 permission_service 仍会继续判断（ASK/DENY 照常触发），web 宿主无法真正放行。现：`True` = 宿主显式放行 → 直接 ALLOW 并跳过 permission_service（宿主放行是最高权威）；`False` = 宿主拒绝 → DENY；`None`（或钩子异常） = 宿主无意见 → 交给 SDK 正常判断。符合文档承诺的"安全网关"语义。
- **ASK 无宿主响应时超时自动拒绝（不再无限卡死）**：executor `_on_awaiting_permission` 此前对未响应的 ASK 无限 `time.sleep(0.5)` 轮询，web 无人响应就永久挂起。现 `AskService` 新增 `age()`/`reject()`/`get_timeout()`，executor 在 ASK 等待超过配置超时（默认 300s，AskService 可配）后自动拒绝并回到 `awaiting_llm` 让模型处理，不再无限轮询。
- **Bash 写范围可配（uploads/ 等不再被路径网误拒）**：`Workspace` 新增 `add_writable_root()`/`add_readable_root()`（运行时扩展写/读白名单，幂等；PathService 持活引用即刻生效），宿主可放行 workspace 外目录（如 web 的 uploads/、web_workspace/）。`build_workspace`（web_session 模式）自动把会话目录（含 uploads/、outputs/）纳入写根，不依赖 sandbox_strategy。
- **后台任务 kill/失败状态变化立即通知 Agent**：`TaskKill`/`kill_session` 此前只改状态、不等 wait 线程，且 `_watch` 线程会把 "killed" 覆盖成 "failed"，Agent 无法感知任务被主动关闭。现 kill 先标记 killed 再杀进程、同步 `_finalize`（进完成队列 + 通知订阅者）；executor 注入通知区分 `[后台任务完成/失败/被终止]`。

### Verification

- Full core-only test suite: `633 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.7] - 2026-08-06

### Fixed

彻底修复 MiniMax 400 `tool id not found (2013)`。该错误有三层叠加根因，本版一并修复：

- **① Tool-call id 对齐**：流式解析中 `ToolCall` 与回传历史里的 assistant 消息对 `id` 使用了两套来源——构造 `ToolCall` 时 `acc["id"] or f"call_{idx}_{time.time_ns()}"` 生成 fallback id，而 `ProviderPipeline.build_assistant_message` 读原始 accumulator 的 `acc.get("id") or ""`。当 MiniMax 等厂商偶发在流里不发 tool call 的 `id`（或后到）时，历史 assistant 消息的 `tool_calls[].id` 为空、工具结果消息的 `tool_call_id` 却是 fallback id，二者对不上即被校验拒绝（工具本身执行成功）。现改为在构造 `ToolCall` 前把 fallback id **写回 `acc["id"]`**（两处：`finish_reason=="tool_calls"` 分支 + 流结束兜底分支），accumulator 成为唯一 id 来源，assistant 消息与工具结果的 id 永远一致；provider 给了非空 id 时原样保留。
- **② ContextCompressor 保持工具调用原子组（主因）**：此前 `compress()` 用 `head[:2] + tail[-4:]` 机械切分——当尾部 `tail_keep` 条恰好全是 tool 结果、声明它们的 `assistant(tool_calls)` 消息落在倒数第 `tail_keep+1` 条时，该 assistant 被切进 middle 摘要，留下孤儿 tool 消息；MiniMax 校验 tool 结果的 `tool_call_id` 找不到对应 assistant `tool_calls` 即 400。现新增 `_aligned_split_points()`：切分点若落在 `assistant(tool_calls) + 紧随 tool 结果` 原子组中间，前移到组首（tail 保留整组、head 把整组并入 middle），保证配对不被拆散。同时 head 至少保留到首条 user 消息，不再把用户最初需求切进摘要。
- **③ `context_window` 跟随注入模型（放大器）**：executor 此前硬编码 `settings.model.context_window`（全局默认模型，即 catalog 第一个，如 deepseek-v4-pro 131072），而非宿主注入 `ModelClient` 实际模型的窗口（如 MiniMax-M3 1M），导致压缩在本不该发生的体量就触发，放大结构破坏。现 `NativeFloodAgent._resolve_context_window()` 优先取注入模型 preset 的 `max_context_tokens`，查不到才回退全局默认。

### Verification

- Full core-only test suite: `607 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.6] - 2026-08-06

### Removed

- **移除 `SearchTools` 工具**：工具发现改为与 skill 完全一致的模型——`## 可用工具` 提示目录直接列出全部工具的名称与基本描述（模型无需搜索就知道有哪些可用），需要具体参数、required 与用法时调用 `GetTool(tool_name=...)` 查看并加载。此前 `SearchTools` 要求模型先凭空猜一个关键词再拿子集，模型对工具目录一无所知，只能瞎碰。移除后：
  - `DEFAULT_CORE_TOOLS` / `settings.tool_loading.core_tools` 默认只含 `GetTool`/`GetSkill`；
  - `make_search_tools_tool` 工厂删除，`NativeFloodAgent._register_tool_catalog_tools` 只注册 `GetTool`；
  - progressive 提示目录与未加载工具的错误提示不再引导「先调用 SearchTools」。

### Changed

- **移除工具输出的静默字符截断**（任务质量急转直下的根因）：此前两层截断会先于模型看到结果之前砍掉长工具输出——
  - `base_tools._finalize_tool_output` 对所有工具输出设 8000 字符硬上限，超长即截断为预览 + 文件指针；
  - `ExecutionJournalService.process_tool_result` 对超过 1000 字符的结果只回灌 800 字符摘要 + 归档指针，模型拿不到完整内容。
  现在两层均移除/改造：`_finalize_tool_output` 返回完整输出；`process_tool_result` 模型始终看到完整工具结果（长结果仍额外归档供 `JournalSearch`/`JournalGetFullResult` 回溯，但不再用摘要替换模型可见内容）。上下文上限由 token 级 `ContextCompressor` 兜底（超阈值才压缩中段、保留头部与最近轮次），而非字符数硬截断。
- **`short_description` 剥离参数提示前缀**：`[必填] command: 要执行的 shell 命令。` 这类描述在目录/提示中现在显示为 `要执行的 shell 命令`（剥离 `[必填]/[可选] xxx:` 前缀），让「基本描述」直接读起来像「这个工具是什么」，同时作用于 progressive 系统提示工具目录与 GetTool 结果。

### Verification

- Full core-only test suite: `601 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.5] - 2026-08-06

### Fixed

- **Tool-call argument key sanitization**: `ToolExecutionService` now normalizes model-generated argument key names (strip edge quotes/whitespace, strip intra-key control chars/quotes, drop empty keys) before permission checks, input validation, and execution. MiniMax-M3 and similar models occasionally emit malformed keys like `{"tool_name"": "..."}` (trailing quote); previously tools without a pydantic `args_schema` (`GetTool`/`SearchTools`, system tools, MCP tools) passed them straight into `**kwargs` and crashed with `TypeError: unexpected keyword argument 'tool_name"'`, which models could not self-correct. Sanitized keys now execute normally (or fail with a clear validation feedback). Defense-in-depth: `TOOL_EXECUTION_ERROR` feedback now explicitly hints "参数名可能有多余引号/空白" when the error is `unexpected keyword argument`.
- **exec command-body write-target enforcement**: new `floodmind/agent/runtime/services/exec_write_scanner.py` statically extracts high-confidence write targets from `exec_bash` command bodies (shell `>`/`>>` redirects; PowerShell `Set-Content`/`Add-Content`/`Out-File`/`New-Item`/`Copy-Item`/`Move-Item`/`Remove-Item`/`Set-Item`) and resolves each with `access="write"`; any target outside allowed writable roots is DENIED. This closes the "read-only authorization bypassed by Bash" hole. Wired into both `_impl_exec_bash` (all modes) and `PermissionService._check_exec_policy` (full runtime, hard-deny before the mutating-command ASK). Conservative by design: only absolute/qualified path-looking targets are checked, quoted string literals (`echo "x > y"`, echoed cmdlet text) are not misdetected, `/dev/null`/`NUL` are skipped, and unresolvable targets (e.g. variables) fail open for host tightening via `permission_decision_hook`.
- **folder-first read whitelist includes installed skill registry**: `PathService` now allows reading the `SkillRegistry` discovery roots plus `site-packages/skills` (separately installed skill packages), so agents can directly read installed skill source files (`SKILL.md`/`references/`/`scripts/`) in folder-first mode instead of hitting repeated "not in allowed dir" denials that cause retry death-loops. Read-only; writes are unaffected.
- **PathService read-deny reason now includes actionable guidance**: appended "如为工作区外文件，请先在工作区附件中引用该文件以完成授权".

### Verification

- Full core-only test suite: `598 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.4] - 2026-08-05

### Fixed

- LLM retry now also covers the `create()` connection-establishment stage. `is_retryable_error` recurses into the `__cause__`/`__context__` chain (e.g. `openai.APIConnectionError`'s `str()` is always "Connection error." but the real retryable cause such as "peer closed connection" lives in `__cause__`), and `ModelClient.stream_chat` re-raises retryable errors in all exception handlers (connection + mid-stream) so the original exception chain survives to the executor's retry loop. Non-retryable errors still emit error/timeout events as before.

### Verification

- Full core-only test suite: `573 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.3] - 2026-08-05

### Fixed

- LLM streaming disconnections are now retried: `is_retryable_error` recognizes `closed connection` / `chunked` / `remote protocol` / `peer closed` patterns (e.g. `httpx.RemoteProtocolError: peer closed connection` mid-chunked-read). The executor's existing retry loop already re-invokes `ModelClient.stream_chat` on raised errors and clears partial state, so a network blip no longer fails the whole agent round.

### Verification

- Full core-only test suite: `571 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.2] - 2026-08-05

### Fixed

- Reworded `CreateScheduledTask` tool description to make clear it schedules time-based dispatch only and is not for launching/backgrounding a process now; points the model to `Bash`/shell tools for immediate process execution. (The previous wording with 「后台」misled the model into selecting it for "run a background program" requests.)
- Fixed scheduled-task execution failing with "workspace unknown": `NativeFloodAgent._effective_workspace` now lazily creates a folder-first cwd workspace when neither an explicit workspace nor a contextvar workspace is available (e.g. scheduling runtime creating an agent via `create_flood_agent` without injection), matching the `Agent` wrapper default. Web contextvar-injected path is unchanged; creation failure stays fail-closed.

### Verification

- Full core-only test suite: `569 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.1] - 2026-08-05

### Fixed

- Bare mode (`Agent(bare=True)`) now auto-loads MCP servers configured in `mcp.json`, matching full-runtime behavior. Previously `_init_bare` short-circuited before the MCP block in `_init_tools`, so configured servers were never connected and `_mcp_pool` was never initialized.
- Extracted MCP auto-connect + tool registration into a shared `NativeFloodAgent._load_mcp_tools()` called from both `_init_bare` (before tool catalog registration) and `_init_tools`; failure is non-fatal (logged warning).
- Bare mode now loads skills too: shared `NativeFloodAgent._load_skills()` populates the skill catalog and registers `GetSkill` in both `_init_bare` and `_init_tools`; the bare orchestrator system prompt includes a `## 可用 skills` section. (Skill CRUD management tools remain full-runtime only.)
- Added an autouse test fixture defaulting `settings.mcp.servers` to empty so the SDK suite stays hermetic/portable and does not depend on machine-local MCP scripts.

### Verification

- Full core-only test suite: `567 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.1.0] - 2026-08-05

### Added

- Public `Agent` now supports `bare=False` to request the full NativeFloodAgent runtime (built-in tools, MCP, Skill, permission-ask events, workspace binding) instead of bare embedding only.
- Added compatibility proxies on public `Agent`: `agent.memory`, `agent.session_id`, `agent.clear_memory()`.
- `Agent.stream(msg, **kwargs)` now forwards extra kwargs (`abort_check` / `attachments` / `resume_session_id`) to the underlying runtime.
- MCP: `build_mcp_tool_specs` sanitizes model-visible tool names (`mcp:<server>:<tool>` → `mcp_<server>_<tool>`) for OpenAI-compatible endpoints; bound functions still call with the original colon-delimited full name.
- MCP: `McpClientConnection.is_connected` now checks stdio process liveness (`process.poll()`).
- MCP: `McpClientPool.call_health()` records the most recent per-server tool-call outcome (`ok` / truncated `error`), thread-safe.
- MCP: `McpClientPool.add_server_connected_listener` / `remove_server_connected_listener` notify hosts when a server connects (idempotent, non-blocking).
- `_build_model_info` prefers the host-routed `ModelClient.model_name` and falls back to the SDK default model resolution.

### Changed

- `_handle_disconnect_mcp_server` now cleans up tools using the sanitized name prefix (`mcp_tool_prefix`) so disconnect cleanup matches the sanitized registry keys.

### Verification

- Full core-only test suite: `563 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.0.2] - 2026-08-05

### Added

- Added host-level `permission_decision_hook` to the public `Agent`, `NativeFloodAgent` (bare and full runtime), and `ToolExecutionService`.
  - Signature: `permission_decision_hook(tool_name, tool_input, sdk_decision, permission_policy) -> PermissionDecision`.
  - Runs after the SDK's base permission decision; host can keep DENY/ASK, or upgrade ALLOW to ASK (interactive `permission_ask`) / DENY.
  - Monotonic guard: the hook can only tighten, never loosen, SDK security decisions (path/dangerous-command/sub-agent tier/planning hard gates cannot be bypassed).
  - Fail-safe: hook exceptions or invalid return values preserve the SDK's original decision.
  - Traces record the post-hook final decision so logs match behavior.
- Wired the global `AskService` into bare-mode `ToolExecutionService` so hook-upgraded ASK can run the `permission_ask` → respond flow in bare mode.
- Passed `permission_handler` through to full-runtime `ToolExecutionService` too, so `Agent(permission_handler=...)` behaves consistently in bare and full modes.

### Verification

- Full core-only test suite: `544 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.

## [1.0.1] - 2026-08-04

### Added

- Added SDK-first folder workspace defaults: `Agent` now binds a folder-first workspace from the launch cwd when no explicit workspace is provided.
- Added `.floodmind/` managed layout for sessions, artifacts, tmp files, scripts, and sandboxes under the active workspace.
- Added SDK purity tests covering top-level import boundaries and core dependency metadata.
- Added neutral runtime adapter modules with legacy Flask/SSE shim modules kept as compatibility aliases.

### Changed

- Changed default dependency surface to SDK/core-only; Web/TUI dependencies live behind optional extras.
- Changed CLI Web/TUI commands to legacy notice-only behavior instead of starting old UI stacks.
- Changed file tools and Bash workspace handling to route path/cwd/workdir resolution through runtime path and permission services.
- Changed artifact watching to focus on the workspace artifact directory instead of treating the workspace root as generated output.

### Fixed

- Fixed recursive checkpoint file snapshots by making checkpoints state-only.
- Removed the file snapshot parameter from `CheckpointService.save()`; checkpoints now persist only `state.json` and `manifest.json`.
- Fixed legacy Web adapter tests so they skip in SDK/core-only environments without Flask.

### Verification

- Full core-only test suite: `532 passed, 1 skipped`.
- The single skipped test is legacy Web adapter compatibility that requires optional `floodmind[web]` / Flask extra.
