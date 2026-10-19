# 06. 工具、技能与 MCP

> 代码位置：`floodmind/tools/`、`floodmind/skills/`、`skills/`、`floodmind/agent/mcp_client.py`、`floodmind/agent/native/tool_runtime.py`、`floodmind/agent/native/tool_loading.py`、`floodmind/agent/runtime/services/tool_execution_service.py`。
> 结论先行：运行时只把工具当作“文本 + JSON Schema + 权限策略 + 可调用函数”，不执行 Skill 里的自然语言指令；Skill 脚本只能经 `Bash` 等受权限约束的工具执行。

## 1. 工具契约

### 1.1 两个类型

| 契约 | 位置 | 角色 |
|---|---|---|
| `AgentTool` | `tools/agent_tool.py:65-92` | 工具作者层：pydantic 模型，宿主/内置/插件用 |
| `ToolSpec` | `agent/runtime/contracts/tools.py:42-53` | 运行时唯一表示：dataclass，带 `func` 与权限策略 |

仓库中不存在 `FunctionSchema` 类型。模型看到的 “function schema” 是 `ToolSpec.to_openai_tool()` 生成的 `{"type":"function","function":{...}}`（`contracts/tools.py:78-86`）。

### 1.2 转换点

- 普通工具唯一入口：`AgentTool.to_tool_spec()`（`agent_tool.py:94-128`）。schema 优先级：原始 `parameters` > `args_schema.model_json_schema()` 清洗 > 空 object；`func` 为空时注入 `_no_impl`。
- `native_from_agent_tool()` 是薄包装（`native/tool_runtime.py:16-40`），`_InstanceToolRegistry.register()` 统一调用它。
- 有意绕过的一处例外：
  - MCP：`build_mcp_tool_specs()` 直造 `ToolSpec`（`mcp_client.py:430-462`）。
  - Handoff：`_install_handoff_tools()` 直造 `ToolSpec`（`executor.py:267`）。
- `_InstanceToolRegistry.tools_schema()` 批量投影（`native_flood_agent.py:136-142`）。

### 1.3 行为标记

| 字段 | 含义 |
|---|---|
| `is_readonly` | 只读 |
| `is_destructive` | 破坏性 |
| `is_concurrency_safe` | 可并发 |
| `check_permissions_fn` | 工具级权限检查 |
| `validate_input_fn` | 输入校验 |
| `permission_policy` | 运行时权限类别 |

`ToolSpec.check_permissions()` 的 fallback 顺序：`check_permissions_fn` → `permission_policy` 交给 RuntimeContext 的 `PermissionService`；有策略但无服务时 **DENY**；两者都没有时 **ALLOW**（`contracts/tools.py:55-72`）。

### 1.4 校验与执行顺序

`ToolExecutionService._execute_bound()`：

1. 键清洗 `_sanitize_arguments`（`tool_execution_service.py:216`）。
2. 完整 JSON Schema 校验（221，实现在 `_validate_raw_parameters`）。
3. 权限检查（262，`_check_permissions` 708；`PermissionService.check` 97-154）。
4. 宿主 `permission_handler` 预授权与 `permission_decision_hook`（720-835，只能收紧）。
5. 幂等 replay（472-488）。
6. 线程池执行，300s 超时（`TOOL_TIMEOUT_SECONDS=300` 135；执行 503-546）。
7. 输出上限：超过 30000 字符时原子卸载全文到 `<state_dir>/tool_outputs/`，返回头尾摘要与 `full_ref`（548-559；`tool_output_service.py:20-88`）。

### 1.5 输出限制（分散在各工具）

`_finalize_tool_output()` **不截断、不落盘**，只生成调用签名并做重试保护：同一调用连续失败 3 次后追加提示并重置计数（`base_tools.py:262-264` 与 `_apply_retry_guard`）。

| 工具 | 限制 |
|---|---|
| `Glob` | 最多 100 条（`file_tools.py:205`） |
| `Grep` | `max_results` 钳制 1-200，默认 50；输出行 800/300 字符（402-404、451-453） |
| `Read` | offset=1/limit=2000，最大 10000；单行超 2000 字符截断；二进制扩展名阻断（479-580） |
| `Bash` | `_BoundedOutput` 64 KiB；timeout 钳制 `[1,240]`（`base_tools.py:543-576`） |
| `WebFetch` | 1000-50000 字符（`base_tools.py:1243`） |
| `ToolExecutionService` | 30k 字符统一上限，超限卸载并返回 `full_ref`（`tool_execution_service.py:548-559`） |

`TOOL_DEFAULTS`（`max_output_length/timeout/retry`，`agent_tool.py:306-310`）没有任何消费者，是死配置。

## 2. 内置工具清单

“默认注册”指 `NativeFloodAgent` 的 orchestrator registry；specialist registry 另有一套安全子集。

### 2.1 文件与执行

| 名称 | 实现 | 权限 | 默认注册 |
|---|---|---|---|
| `Glob` | `file_tools.py:228` | 只读 | 是 |
| `Grep` | `file_tools.py:459` | 只读 | 是 |
| `Read` | `file_tools.py:577` | 只读，二进制阻断 | 是 |
| `Write` | `file_tools.py:671` | `write`，原子写 | 是 |
| `Edit` | `file_tools.py:814` | `write`，原子写 | 是 |
| `ApplyPatch` | `file_tools.py:1114` | `patch`，删除/移动需 ASK | **否**（仅全局 ToolRegistry） |
| `Bash` | `base_tools.py:850` | `exec`，destructive，非并发安全 | 是 |
| `GetSkill` | `base_tools.py:451` | `readonly` | 是（`_load_skills` 与 `all_tools` 各注册一次，按名覆盖） |

### 2.2 记忆与检索

| 名称 | 实现 | 权限 | 默认注册 |
|---|---|---|---|
| `MemoryAdd` | `base_tools.py:1435` | `state_write` | 是；specialist 排除（写入门面语义存储，fail-closed） |
| `MemorySearch` | `base_tools.py:1497` | 只读 | 是（specialist 排除） |
| `ConversationSearch` | `memory_tools.py:71` | 只读 | 是（specialist 排除） |
| `ExperienceSearch` | `memory_tools.py:108` | 只读 | 是（走全局经验库单例） |
| `CoreMemoryAppend` | `memory_tools.py:152` | `state_write` | 是；specialist 排除（state_write） |
| `CoreMemoryRead` | `memory_tools.py:192` | 只读 | 是 |
| `JournalSearch` | `memory_tools.py:272` | 只读 | 是（读 canonical Journal 投影） |
| `JournalGetFullResult` | `memory_tools.py:318` | 只读 | 是（`full_ref` 由工具输出卸载产生） |

### 2.3 网络、任务与项目

| 名称 | 实现 | 权限 | 默认注册 |
|---|---|---|---|
| `WebSearch` | `base_tools.py:1196` | `network` | `enable_search=True` 时 |
| `WebFetch` | `base_tools.py:1351` | `network`，SSRF 前缀阻断 | 同上 |
| `UpdateProjectInstructions` | `base_tools.py:1700` | `ask` | 是（specialist 排除） |
| `CreateScheduledTask` | `base_tools.py:1761` | `state_write` | 是；specialist 排除（state_write） |
| `ListScheduledTasks` | `base_tools.py:1806` | 只读 | 是 |
| `CancelScheduledTask` | `base_tools.py:1840` | `state_write` | 是；specialist 排除（state_write） |
| `TaskOutput` | `base_tools.py:942` | 只读 | 是 |
| `TaskList` | `base_tools.py:953` | 只读 | 是 |
| `TaskKill` | `base_tools.py:964` | `exec`，destructive | 是（specialist 排除） |

### 2.4 任务经验

`SearchTaskExperience`、`BrowseExperienceTree`、`DrillDownExperience`、`AddTaskExperience`，通过 `make_task_experience_tools(store, skill_owner=...)` 绑定当前 Agent 的 owner-scoped store（`base_tools.py:1906-1993`，接入 `native_flood_agent.py:901-905`）。

### 2.5 编排与规划

| 名称 | 实现 | 权限 | 说明 |
|---|---|---|---|
| `create_plan` | `native_flood_agent.py:1061` | `state_write`，`allow_in_planning` | 规划模式可用 |
| `update_plan` | `native_flood_agent.py:1115` | 同上 | |
| `exit_plan_mode` | `native_flood_agent.py:1186` | `ask` | 请求退出规划 |
| `SubAgent` | `native_flood_agent.py:1203` | `internal`，destructive | orchestrator 预载；子代理内禁止级联 |
| `ParallelTask` | `native_flood_agent.py:1222` | `internal`，destructive | 同上 |
| `GetTool` | `tool_loading.py:343` | 只读（会改 loader 状态） | 两个 registry 各一个 |

### 2.6 Skill 与 MCP 管理

| 名称 | 实现 | 权限 | 说明 |
|---|---|---|---|
| `LoadMcpServer` | `native_flood_agent.py:934` | `network` | 仅 orchestrator；子代理 DENY |
| `ListMcpServers` | `native_flood_agent.py:958` | 只读 | 仅 orchestrator |
| `DisconnectMcpServer` | `native_flood_agent.py:969` | `state_write`，destructive | 仅 orchestrator |
| `ListSkills` | `native_flood_agent.py:987` | 只读 | 仅 orchestrator |
| `CreateSkill` | `native_flood_agent.py:997` | `state_write` | 原子写，有回读校验 |
| `UpdateSkill` | `native_flood_agent.py:1017` | `state_write` | 原子写，有回读校验 |
| `RemoveSkill` | `native_flood_agent.py:1036` | `state_write`，destructive | 归档目录 |
| `RefreshSkills` | `native_flood_agent.py:1050` | `state_write` | 重扫并重建 prompt |

### 2.7 注册链

```text
NativeFloodAgent.__init__
  ├─ create_skill_registry(...)                 :365-372
  ├─ SkillCurator(registry=...)                 :373
  ├─ TaskExperienceCapture.bind_skill_generation :378-385
  └─ ToolLoader（orchestrator/specialist）      :440-456
        ↓
_init_tools()                                   :795
  ├─ PermissionService + 子代理 deny 规则        :824-880
  ├─ _load_skills()                             :767-788
  ├─ all_tools 内置工具注册                      :885-907
  ├─ 实例绑定经验工具                            :901-905
  ├─ WebSearch/WebFetch（enable_search）        :906-908
  ├─ specialist 安全子集                        :919-926
  ├─ _load_mcp_tools()                          :930
  ├─ MCP/Skill CRUD/规划/委派工具                :934-1250
  ├─ plugin                                     :1262-1266
  ├─ _register_tool_catalog_tools（GetTool）      :1266（定义 1313-1324）
  └─ host tools                                 :1270-1283
```

**关键事实**：native runtime 使用 `_InstanceToolRegistry`，从不读取全局 `ToolRegistry`。`base_tools._register_all_tools()` 在模块导入时执行（2059 附近），只服务全局镜像、测试与 `make_task_experience_tools`。新增工具只改 `base_tools` 不会生效。

## 3. 权限

### 3.1 权限工厂（`agent_tool.py:167-224`）

| 工厂 | 行为 |
|---|---|
| `make_readonly_permission_fn()` | 只读放行 |
| `make_write_permission_fn(path_field="file_path")` | 写路径校验 |
| `make_exec_permission_fn(...)` | 命令执行校验 |
| `make_skill_script_permission_fn()` | 技能脚本 |
| `make_ask_permission_fn(reason)` | 强制询问 |
| `make_read_path_permission_fn(path_field)` | 读路径 |

路径解析经 `resolve_tool_path`（`agent_tool.py:247`）/ `check_path_permission`（318）落到 `PathService`。

### 3.2 判定层序

`PermissionService.check()`（`permission_service.py:97-154`）：

```text
策略 DENY
  → 子代理 tier
  → planning mode
  → 全局 deny
  → ASK / host 预授权
  → 全局 allow
```

- 子代理硬门：禁用 `WebFetch` 与所有 `network` 策略（MCP/内置网络工具）；ASK 降级 DENY；只允许 `readonly/read_path/write/delete/move/patch/exec/internal`（83-95、156-196）。`state_write` 不在白名单，子代理必然拒绝。
- 规划模式：拒绝 `write/delete/move/patch/exec/state_write/skill_script`；`allow_in_planning=True` 的 `state_write` 例外（207-230）。
- 宿主 `permission_handler` / `permission_decision_hook` 只能收紧，不能越过 tier/planning/路径/危险命令/deny（`tool_execution_service.py:708-835`）。
- `state_write` 在策略层直接 ALLOW，安全性依赖 tier/planning/deny 规则。

## 4. Skill 体系

### 4.1 发现根与优先级

优先级：`builtin 500 > host 400 > project 300 > claude_compat 200 > ephemeral 100`（`registry.py:29-35`）。

默认根（`registry.py:86-101`）：

1. `floodmind/skills` — builtin，只读；
2. `skills/` — project，可写；
3. `.claude/skills` — claude_compat，只读。

`create_skill_registry(additional_roots, writable_root)` 把额外根标为 host 只读，自定义 writable 作为 host 可写根（498-525）。`NativeFloodAgent` 为每个 Agent 创建独立 registry，并注入 `PermissionService().scan_content_threats`（`native_flood_agent.py:363-372`）；全局单例 `get_skill_registry()` 不在运行时路径上。

### 4.2 目录结构

```text
<skill-root>/<skill-name>/
  SKILL.md            必需，YAML frontmatter
  scripts/*.py        非 _ 开头
  references/*.md|txt|pdf
  assets/*
  tools.py            可选
```

### 4.3 发现与合并

- `discover_skills()` 只匹配 `*/SKILL.md`，拒绝目录级 symlink/junction（`base.py:214-246`）。
- `_parse_skill_md()` 要求首行 `---`、frontmatter 与 `name`；无脚本且无 `provides_tools/tools.py` 标为 knowledge-only；body 超 1500 行警告（84-194）。
- 合并“先到先得”：按 `(-priority, order)` 排序，同名保留高优先级，后扫描根记 warning；disabled 过滤；ephemeral 仅在磁盘同名不存在时加入（`registry.py:232-279`）。
- `refresh()` 重扫（461）；`_rescan_and_swap()` 用 revision 防并发覆盖（281-302）；变更通过回调通知，native 订阅后重建 prompt（304-339；`native_flood_agent.py:1612`）。

### 4.4 查询与 CRUD

- 查询：`all_skills/get_skill/get_skill_root/get_skill_source/get_skill_content/load_skill/list_skills/catalog`（353-404）。
- 名称校验：空、`.`/`..`、前导点、前后空白/尾点、`<>:"/\|?*`、控制字符、Windows 保留名（407-421）。
- 可写路径：拒绝 symlink/junction，`resolve()` 后必须仍在 writable root 下（423-458）。
- `register_skill` 写 ephemeral；即使被磁盘同名遮蔽也保留自己的条目（467-485）。
- `set_disabled` 修改禁用集并重扫（487-495）。

### 4.5 GetSkill

`make_get_skill_tool()`（`base_tools.py:399-470`）：

- `lru_cache(maxsize=128)` 缓存渲染结果。
- 未命中记录失败使用并返回可用技能列表；命中记录成功使用。
- 注册 refresh 回调清缓存；`cleanup()` 幂等退订。
- `_render_skill` 只输出说明、脚本路径、参考文档、资源路径，**不会自动执行脚本**（352-396）。

### 4.6 策展器

`SkillCurator`：

| 能力 | 说明 |
|---|---|
| 阈值 | stale 30 天、archive 90 天（107-108、139-140） |
| 状态文件 | `writable_root/.floodmind/skill-curator.json` |
| 归档根 | `writable_root/.archived` |
| 使用记录 | `record_usage` 累计 success/failure 与 last_used |
| 陈旧/归档 | `find_stale_skills` / `find_archive_candidates` |
| 重复检测 | 字符 bigram Jaccard |
| 归档/恢复 | 拒绝覆盖同名归档 |
| 维护 | 6 小时间隔；`run_maintenance_if_needed`；native 启动时触发（`native_flood_agent.py:912`） |

当前磁盘可见的重复/残留：`skills/.floodmind/skill-curator.json`、`skills/.floodmind/.last-skill-maintenance`、`skills/.last_skill_maintenance`（旧标记）、`.floodmind/skill_curator.json`（旧全局状态）。

### 4.7 安全

- 目录 symlink/junction：发现与写路径均拒绝。
- 名称与路径穿越：`validate_skill_name` + `validate_writable_skill_path`。
- 内容威胁扫描：`PermissionService.scan_content_threats`（`permission_service.py:537`）；`_parse_skill_md` 命中则跳过加载（`base.py:178-188`）。
- 只读根不可写（224-230）。

### 4.8 当前实际可见 Skill

| 根 | 内容 |
|---|---|
| builtin | `csv`、`data-analysis`、`doc-coauthoring`、`docx`、`mcp-builder`、`pdf`、`plotting`、`pptx`、`skill-creator`、`xlsx` |
| project | `shuge-greeting` 有效；`test-skill` 的 SKILL.md 内容只有 `test`，解析失败；`skills/` 下另有 `csv/docx/mcp-builder/pdf/pptx/skill-creator/xlsx` 与 builtin 同名，被 builtin 遮蔽 |
| claude_compat | `taste-skill`、`tui-design` |
| contrib（不在默认根） | `contrib/chronos`、`contrib/skills/aojiang-hydro`，只能经 `skill_roots` 注入；脚本还引用了不存在的 `floodmind.skills.chronos_pipeline` / `floodmind.skills.hydro_case_client` |

### 4.9 未接线能力

`Skill.load_tools_module()` 已实现（`base.py:60-78`），但全仓无调用；`provides_tools` 只用于 catalog 标记和 knowledge-only 判定。声明 `provides_tools` 或放 `tools.py` 不会自动注册工具。

## 5. MCP

### 5.1 连接

`McpClientConnection`（`mcp_client.py:81`）：

| 项 | 事实 |
|---|---|
| 协议版本 | `2024-11-05` |
| `transport="sse"` | 实际是 Streamable HTTP，名称仅兼容；必须提供 `url` |
| `transport="stdio"` | 启动子进程；stderr 排空 + stdout 读取线程；支持 wall-clock 超时；断开时终止进程树 |
| JSON-RPC | HTTP 校验响应 id；SSE/JSON 双形态；stdio 事务串行化并按 id 过滤 |
| 连接判定 | stdio 看进程存活；HTTP 为 best-effort |

### 5.2 工具映射

`build_mcp_tool_specs()` 是唯一构造点（`mcp_client.py:430-462`）：

- 注册名（模型可见）：`_mcp_tool_spec_name()` 把 `mcp:server:tool` 清洗为 `[a-zA-Z0-9_-]+`，即 `mcp_server_tool`。
- 实际调用名仍是 `mcp:server:tool`，由闭包交给 `call_tool_fn`。
- 所有 MCP 工具：`is_readonly=False`、`is_destructive=True`、`permission_policy=network`；stdio 工具 `is_concurrency_safe=False`。
- `_McpToolCallResult` 是 `str` 子类，携带 `is_error/error/raw`。

### 5.3 池

`McpClientPool`（467）提供 `connect_server/connect_all/connections/get_all_tools/call_tool/call_health/disconnect_all/list_servers/get_server_info/disconnect_server`。

- `connect_server` 显式 name 冲突抛错；未命名用 `mcp-dynamic-N`；连接成功后才入池。
- `connect_all` 逐 server 捕获异常，单个失败只 warning。
- 连接成功触发 `server-connected` 监听器。
- `disconnect_server` 只断连接，不清理 registry；Agent 用 `_mcp_tool_names` 精确 `unregister_names` 后刷新 schema/prompt（`native_flood_agent.py:1691-1710`）。
- 全局单例 `get_mcp_client_pool()` 被 SDK 导出，但 Agent 默认自建 pool（`native_flood_agent.py:393-405`）。

### 5.4 接入

```text
_load_mcp_tools()                         native_flood_agent.py:725-765
  → 读 settings.mcp.servers；配置非法直接 McpConfigError
  → pool.connect_all()
  → 对已连接且配置过的 server 调 _register_mcp_connection
  → 单个注册失败：断开该 server 并 raise
  → 外层 catch 只 warning

LoadMcpServer                            native_flood_agent.py:933
  → 校验 name/transport/url/command
  → pool.connect_server
  → _register_mcp_connection（名称冲突原子失败、注册到两个 registry）
  → _refresh_mcp_catalog
```

**配置位置与键名**：SDK 只读 `get_floodmind_home()/mcp.json`（`settings.py:664`）。`get_floodmind_home()` 的顺序是 `FLOODMIND_HOME` > 活跃 profile 目录 > `~/.floodmind`（`settings.py:72-88`）。默认文件当前使用正确的 `"transport": "stdio"`。`MCP_SERVERS` 环境变量可整体覆盖 servers 数组。

仓库内还保留一份项目级 `.floodmind/mcp.json`，默认不会被加载；`load_mcp_config()` 在每次加载时检测到它会显式告警并给出正确路径（`settings.py:715-731`）。只有当 `FLOODMIND_HOME` 指向该目录时它才会生效，而 `_validate_mcp_servers()` 会因 `"type"` 字段直接抛 `McpConfigError`（`settings.py:681-687`）；`connect_server` 也只认 `transport`（`mcp_client.py:485-490`）。排障时先确认实际加载的是哪一份。

## 6. 风险

| # | 风险 | 级别 | 影响 |
|---|---|---|---|
| 1 | 两份 `mcp.json` 且键名不一致（`type` vs `transport`） | 中 | 项目级配置默认不加载（每次加载告警）；一旦把 `FLOODMIND_HOME` 指过去，启动即 `McpConfigError` |
| 2 | `ApplyPatch` 只注册到全局 ToolRegistry | 中 | native 运行时不可见，UI 标签却存在 |
| 3 | 全局 `ToolRegistry` 与 `_InstanceToolRegistry` 双事实源 | 中 | 持续产生“注册但不可见”的漂移 |
| 4 | `JournalGetFullResult` 返回的是事件里的有界 `result_summary` | 低 | 工具名暗示“完整结果”，实际全文在 `full_ref` 卸载文件，需要 `Read` |
| 5 | MCP `is_error` 不进入执行失败状态 | 中 | MCP 报错被标记 completed，不触发 retry/journal failed |
| 6 | MCP 注册失败中止后续 server | 中 | 后续 server 已连接但无工具，留在池中 |
| 7 | `ExperienceSearch` 走全局经验库单例 | 中 | 与 owner store 的注入/捕获可能不是同一库 |
| 8 | contrib Skill 导入不存在的模块且不在默认根 | 中 | 无法按默认路径使用 |
| 9 | Skill `tools.py`/`provides_tools` 未接线 | 中 | 声明能力不会注册工具 |
| 10 | GetTool 的 MCP 示例名与注册名不一致 | 中 | progressive 模式下按提示加载会失败 |
| 11 | `AgentInfo` 的 allow/deny 未被执行 | 中 | plan/explore 类型不限制工具集（仅 prompt 生效） |
| 12 | MCP 工具注册到 specialist，但 tier 必然拒 network | 低 | 可见但不可用，造成无效尝试 |
| 13 | Skill 归档 symlink 检查弱于 registry | 低 | junction 可能归档错误目录 |
| 14 | `skill_script` 权限类型与硬编码根未用于实际工具 | 低 | 自定义 skill root 不受该策略保护 |
| 15 | `TOOL_DEFAULTS` 死配置 | 低 | 误以为改它能统一限制 |
| 16 | GetSkill 重复注册 | 低 | 维护噪声（按名覆盖，无功能影响） |
| 17 | SDK 导出的 MCP 单例与实际 pool 不一致 | 低 | 宿主管理不到 Agent 实际连接 |
| 18 | `base_tools.py` 4000+ 行 | 低 | 改动影响面大 |
| 19 | Skill 脚本（docx/pdf/pptx/xlsx/mcp-builder/skill-creator）无测试 | 低 | 回归无保护 |

## 7. 修改影响提示

| 变更 | 必查 |
|---|---|
| 新增/删除内置工具 | 同时改 `base_tools._register_all_tools()` 与 `native_flood_agent.py` 的 `all_tools`；检查 `_is_specialist_builtin_safe` |
| 改 `AgentTool`/`ToolSpec` 字段 | `agent_tool.py:94-128`、`contracts/tools.py:42-86`、`tool_runtime.py:16-40`、`mcp_client.py:452-461`、`executor.py:273-287`、`_InstanceToolRegistry` |
| 改权限策略 | 工具注解、`PermissionService.check/check_tool_policy`、子代理 tier、planning gate、specialist 过滤、`test_permission_service.py` |
| 改 MCP 配置 | `settings.load_mcp_config`（737-782）、`connect_server` 的 `"transport"`、`LoadMcpServer` schema、`.floodmind/mcp.json` |
| 改 MCP 命名 | `_mcp_tool_spec_name`、`mcp_tool_prefix`、GetTool 描述、`DisconnectMcpServer` 清理、UI 展示 |
| 改 Skill 根/优先级 | `default_roots`、`create_skill_registry`、curator 状态/归档根、维护标记文件、contrib 注入 |
| 改 Skill CRUD | 原子写、写前威胁扫描、写后解析校验与回滚、返回文案基于 registry 实际结果 |
| 改 GetSkill 缓存 | refresh 回调 `cache_clear` 与 `cleanup()` 订阅/退订必须对称 |
| 改输出限制 | 改各工具 `_BoundedOutput`/`limit`/`max_results`，或 `tool_output_service.DEFAULT_MAX_CHARS`；不要改 `_finalize_tool_output` |
| 改 `full_ref` | `ToolExecutionService` 产出 metadata，executor 写入 `tool.execution.*` payload，`JournalGetFullResult` 按该字段反查 |
| 改工具名 | 同步 `gateway/chainlit_app.py`、`profile/guidance.py`、系统提示 |

## 8. 验证命令

```bash
python -m pytest tests/test_agent_tool.py tests/test_base_tools.py tests/test_file_tools.py \
  tests/test_tool_execution_service.py tests/test_permission_service.py -q

python -m pytest tests/test_skill_registry.py tests/test_skill_curator.py \
  tests/test_get_skill_tool_factory.py tests/test_agent_skill_roots.py -q

python -m pytest tests/test_mcp_client.py tests/test_mcp_agent_isolation.py -q

python -m evals.runner --tier tool   # 真实工具层：真实 ToolExecutionService + 真实文件工具
```

评测覆盖：

- `evals` 的 `tool` 层声明 `"tool_mode": "real"`，只替换模型客户端，工具走生产 `ToolExecutionService` 与真实权限检查；覆盖 fixture 读取、Glob+Grep 检索、Write 后 Read 回读校验、越界写入拒绝。`smoke`/`regression` 层用脚本轨迹覆盖工具往返、失败恢复、非法参数与中断。
- `benchmarks/multi_agent` 对比 single 与 delegated 两种策略，用于判断任务是否值得拆分；默认离线只列任务，`--live` 才真实调用模型，CI 不运行 `--live`。它验证的是委派收益，不覆盖具体工具实现。
