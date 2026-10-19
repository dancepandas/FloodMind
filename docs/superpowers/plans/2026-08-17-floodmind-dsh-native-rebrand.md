# FloodMind dsh 原生化 + 换皮实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 FloodMind 从「Python SDK + 自包含 cordis + wheel/Docker」收敛为「dsh 原生 profile + 换皮 web UI + 预置记忆/技能管理/MCP 管理插件 + 水文 skills」，以 MIT 打包 dsh 再分发。

**Architecture:** FloodMind 不再是 Python 产品，而是 dsh 生态上的一等公民：一个 `@floodmind/dsh-bundle`（含 floodmind profile + 换皮皮肤）+ 水文 skills 资产 + 两个预置插件（dsh-mnemon 记忆、dsh-extension-hub 技能/MCP 管理）。dsh 运行时（MIT）整体打包进发行物，保留 LICENSE 与版权声明。

**Tech Stack:** dsh（deepseek-harness，MIT）、Cordis bundle/patch/profile、dsh-mnemon（+ Mnemon CLI）、dsh-extension-hub、skills（SKILL.md 资产）、`--dsw-*` design token 皮肤。

## Global Constraints

- dsh 是 MIT，打包再分发必须保留 `LICENSE` 与版权声明；FloodMind 自己的内容可另授权。
- 不再自建记忆 / MCP / 技能引擎——全部复用社区/官方插件，FloodMind 只做选型 + 锁版本 + 契约测试。
- RAG 不内置：从组合中移除 `mcp-rag` 行与 `FLOODMIND_RAG_MCP_URL` 契约；文档给「用户自接 MCP」示例。
- 只保留真正 FloodMind 差异，删除与 dsh base/web bundle 重复的组合行（逐行归属见 Task 2）。
- Windows + Linux 均需可跑（dsh-mnemon 的 Mnemon CLI 在 Windows 走官方 release zip，自动发现）。
- 模块化、标准化，禁止打补丁式修改。
- 版本锁定：dsh 与各预置插件均锁到明确版本，写入 bundle 的 package.json。

---

## 现状（执行前必须记住）

- 当前分支 `feat/dsh-ts-sdk`，v3.0.0，green lane 61 passed / 1 skipped（工作区）+ 干净 archive 43/19。
- 当前形态：`floodmind/` Python 包（三符号 facade）+ `floodmind.cordis.yml` 自包含组合 + pyproject wheel + Dockerfile + 104 退役测试台账 + 收敛/领域契约测试。
- `floodmind.cordis.yml` 里大量行是 dsh base/web 已提供的（sandbox/shell/session/checkpoint/fs/subagent/todo/compaction 等），真正 FloodMind 差异只有：minimax provider、persona、skill customSkillDirs、dsh-native-memory、mcp-rag。
- skills/ 已是 dsh 标准 SKILL.md 资产（10 个 canonical bundle），可原样保留。

---

### Task 1: 冻结基线 + 建立发行骨架

**Files:**
- Create: `floodmind-bundle/package.json`（`dsh.bundle.patch` 指向 `cordis.patch.yml`；peerDeps 锁 dsh `0.1.0-rc.6`）
- Create: `floodmind-bundle/cordis.patch.yml`（空 insert 骨架，先不写内容）
- Create: `floodmind-bundle/LICENSE`（FloodMind 自身授权）+ `floodmind-bundle/THIRD_PARTY_NOTICES.md`（列 dsh、dsh-mnemon、dsh-extension-hub 及其 LICENSE/版权）

**Interfaces:**
- Produces: `@floodmind/dsh-bundle` 包结构，供后续任务往里填 patch。

- [ ] 在 `floodmind-bundle/` 下建 npm 包骨架，`package.json` 声明 `dsh.bundle.patch` 与 dsh peer 依赖。
- [ ] 写 `THIRD_PARTY_NOTICES.md`，逐条记录 dsh（MIT）、dsh-mnemon（MIT）、Mnemon（Apache-2.0）、dsh-extension-hub 的版权与 license 全文路径。
- [ ] 提交。

---

### Task 2: 内容归位——floodmind.cordis.yml 差异行 → bundle patch

**Files:**
- Modify: `floodmind-bundle/cordis.patch.yml`（填入真正差异）
- Reference（只读）: `deepseek-harness/packages/bundle/base/cordis.patch.yml`、`packages/bundle/web-app/cordis.patch.yml`、`packages/bundle/headless/cordis.patch.yml`

**Interfaces:**
- Consumes: Task 1 的包骨架。
- Produces: 一份只含 FloodMind 差异的 patch。

**逐行归属（决定去留）：**

| 现 floodmind.cordis.yml 行 | 处理 |
|---|---|
| sdk-jsonrpc-server | 删（dsh 原生化后不再用自建 JSON-RPC carrier） |
| llm-pi-ai（minimax provider） | **保留**，patch 成 minimax-cn 路由 |
| sandbox/subprocess/bash/pwsh/fs | 删（base 已有） |
| storage/domain/session-query | 删（改由 dsh-mnemon 的 Mnemon 存储承载，见 Task 4） |
| agent-spine persona | **保留**，转成 floodmind persona/workspaceContext |
| sessions/checkpoints | 删（base 已有） |
| dsh-native-memory | 删（Task 4 换 dsh-mnemon） |
| mcp-rag | 删（RAG 不内置） |
| subagent/todo/token-meter/compaction | 删（base 已有） |
| skill-filesystem（customSkillDirs） | **保留**，指向 skills/ 根 |
| shell-env/tool-pwsh | 删（base 已有平台分叉） |

- [ ] 写出 `cordis.patch.yml`，只 insert/patch 上述「保留」行，id 前缀 `floodmind-` 避免与 base 冲突。
- [ ] 用 `dsh --dump-config`（若可用）或人工 diff 核对：patch 后有效组合不重复挂载 base 能力。
- [ ] 提交。

---

### Task 3: 砍 Python facade

**Files:**
- Delete: `floodmind/`（`__init__.py`、`dsh_sdk.py`、`plotting.py`、`scripts/`）
- Modify: `pyproject.toml`（移除 `[project]` 依赖 `deepseek-harness-sdk`/`deepseek-harness-runtime-bin`、`[tool.setuptools]`、`package-data`、`dynamic version`）
- Delete: `setup.py`、`MANIFEST.in`、`Dockerfile`（若仅服务 wheel 分发）
- Modify/Delete: `tests/convergence/test_public_sdk.py`（facade 契约）、`tests/test_sdk_purity.py` 中关于 facade/import 无副作用的用例
- Modify: `.env.example`（移除 `FLOODMIND_*`、`DSH_CORDIS_CONFIG` 等 SDK 专属项）

**Interfaces:**
- Consumes: 无。
- Produces: 仓库不再有 Python 产品面；`from floodmind import ...` 彻底移除。

- [ ] 删除 floodmind 包与 facade 契约测试。
- [ ] 清理 pyproject/setup.py/MANIFEST.in/Dockerfile。
- [ ] 全量跑 `python -m pytest tests/test_sdk_purity.py tests/convergence tests/domain`，确认剩余测试（skills/领域契约）仍绿，且无 import floodmind 引用。
- [ ] 提交。

---

### Task 4: 预置记忆插件 dsh-mnemon

**Files:**
- Modify: `floodmind-bundle/cordis.patch.yml`（insert `id: floodmind-mnemon, name: dsh-mnemon`）
- Modify: `floodmind-bundle/package.json`（声明 `dsh-mnemon` 依赖 + `mnemon` CLI 版本要求）
- Create: `docs/mnemon-setup.md`（Windows/macOS/Linux 安装 Mnemon CLI 的步骤）

**Interfaces:**
- Consumes: Task 2 的 patch 骨架。
- Produces: floodmind profile 开箱即有记忆能力。

- [ ] 在 patch 中 insert `dsh-mnemon` 行（config: recallMode/writebackMode/storageScope 等，storageScope 默认 workspace）。
- [ ] package.json 锁定 `dsh-mnemon@0.1.6`；文档写清 Mnemon CLI v0.2.3+ 的 Windows zip / brew / go install 三种装法。
- [ ] 写契约测试：bundle patch 含 dsh-mnemon 行、package.json 版本锁定、docs 覆盖三平台安装。
- [ ] 提交。

---

### Task 5: 预置技能/MCP 管理插件 dsh-extension-hub

**Files:**
- Modify: `floodmind-bundle/package.json`（声明 `dsh-extension-hub` 依赖，锁 0.1.4）
- Modify: `floodmind-bundle/cordis.patch.yml`（如 extension-hub 走 profile patch 而非 bundle patch，则改为在 profile 安装说明中列出）
- Modify: `docs/`（安装后 Settings → Extension Management 用法）

**Interfaces:**
- Consumes: Task 2。
- Produces: floodmind 开箱即有技能/MCP 管理 UI。

- [ ] 确认 dsh-extension-hub 的接入方式（bundle patch insert vs `dsh plugin add`），按官方方式接入并锁版本。
- [ ] 写契约测试：扩展管理插件已声明、版本锁定。
- [ ] 提交。

---

### Task 5b: 预置视觉插件 dsh-vision-toolkit

**Files:**
- Modify: `floodmind-bundle/package.json`（声明 `dsh-vision-toolkit` 依赖，锁版本）
- Modify: `floodmind-bundle/cordis.patch.yml`（如走 patch 则 insert；否则按官方 `dsh plugin add` 方式接入）
- Modify: `docs/`（vision endpoint 配置说明）

**选型**: `dsh-vision-toolkit`（Anionex/dsh-vision-toolkit，MIT，542★，10 个视觉工具）。**两个硬依赖**：managed Python 3.11+ runtime（发行物多带一个 Python）+ 一个 OpenAI-compatible 识图 endpoint（额外 key）。

**Interfaces:**
- Consumes: Task 2。
- Produces: FloodMind 模型可看图（图片问答 / 长截图 OCR / UI 还原）。

- [ ] 接入 dsh-vision-toolkit 并锁版本。
- [ ] 文档写清两个依赖（Python runtime + vision endpoint），并标注 Windows 下的 Python runtime 获取方式。
- [ ] 写契约测试：vision 插件已声明、版本锁定。
- [ ] 提交。

---

### Task 6: web UI 换皮（floodmind 皮肤）

**Files:**
- Create: `floodmind-bundle/theme/`（覆盖 `--dsw-*` token 的 CSS + logo + 标题/品牌文案）
- Create: `floodmind-bundle/palette.md`（品牌色 token 表，作为唯一真源）
- Modify: `floodmind-bundle/cordis.patch.yml` 或 theme 注入方式（挂 floodmind 皮肤）

**Interfaces:**
- Consumes: Task 2。
- Produces: 加载 floodmind profile 后 UI 呈现 floodmind 品牌。

- [ ] 定义品牌色 token（基于 dataviz 设计规范：主色、深浅两套、语义色），写 `palette.md`。
- [ ] 产出覆盖 `--dsw-*` 的皮肤资源与 logo/标题替换。
- [ ] 验证：`dsh --profile floodmind`（或 web）加载后 UI 为 floodmind 视觉。
- [ ] 提交。

---

### Task 7: 测试改造 + 全量验证

**Files:**
- Modify: `tests/test_sdk_purity.py`、`tests/convergence/*`、`tests/domain/*`——把「facade 存在 / 自包含 cordis / RAG fail-loud」相关断言改成「bundle 契约 / 预置插件锁定 / RAG 已移除」。
- Modify: `tests/legacy_test_ownership.json`（若涉及）。

**Interfaces:**
- Consumes: Task 1–6。
- Produces: 绿色测试套件，覆盖新形态的边界。

- [ ] 改写/新增：bundle patch 契约、预置插件版本锁定、RAG 已移除、skills 资产完整性、三平台 Mnemon 文档。
- [ ] 全量绿（工作区 + `git archive` 干净检出两路）。
- [ ] 更新 README / DEVELOPER_GUIDE / architecture wiki 至「dsh 原生化」形态。
- [ ] 提交。

---

### Task 8: 发行形态——fork 社区桌面壳换皮 + MIT notice

**Files:**
- Create: `desktop/`（fork 自 anywhere-labs/deepseek-harness-desktop）
- Modify: 根 `README.md`（新安装/使用方式）

**选型（desktop 壳）**: fork `anywhere-labs/deepseek-harness-desktop`（MIT，9434★，Electron，支持多 web-capable profile 切换 + 插件管理最完整，DSH pin 0.1.0-rc.6）。备选 ChisaAlter（MIT 101★）、xiincs（MIT 26★）；排除 ningbainb（BSD-3 65★，需保留声明）、hairyf（无明确 license 302★，不可 fork）。

**Interfaces:**
- Consumes: Task 1–7。

- [ ] fork anywhere-labs 桌面壳到 `desktop/`，保留其 LICENSE 与版权声明。
- [ ] 换 logo/标志/标题/默认配置为 floodmind，预置 floodmind profile + 两个插件（dsh-mnemon、dsh-extension-hub）。
- [ ] 确认发行物完整保留 dsh/LICENSE、anywhere-labs/LICENSE、THIRD_PARTY_NOTICES.md。
- [ ] 文档写清两条使用路径：`dsh plugin --profile web add <floodmind-bundle>` 到现有 profile；或 floodmind 桌面壳（fork 产物，双击即用）。
- [ ] 提交。

---

## 自检清单

- [ ] 覆盖目标：Python facade 已删、内容已归位 profile、三插件已预置（mnemon + extension-hub）、皮肤已换、RAG 已移除、MIT notice 已就位。
- [ ] 无占位符：所有任务给了具体文件与操作。
- [ ] 版本一致性：dsh、dsh-mnemon、dsh-extension-hub 版本在 package.json 与契约测试中一致。
