# Skill 体系统一架构详解

> 父文档: [`OVERVIEW.md`](./OVERVIEW.md) | 更新: 2026-07-04 (Skill-A/B/C/D 完成后)

## 重构前（病灶）

```
4 条发散的发现路径:
  skills/__init__.py  → Path.cwd()/skills + Path.cwd()/.claude/skills
  discover_skills()   → floodmind/skills/
  task_experience     → _PROJECT_ROOT/skills (但不在任何发现集中)
  curator             → Path.cwd()/.floodmind/skills (又不同)

双 registry: skills.SKILL_REGISTRY + tools._SKILL_REGISTRY
双 catalog: generate_skill_catalog (无人读) + inline (进 prompt)
死 refresh_skills: stale-binding bug + 零调用方
auto-gen 不可见: 写 PROJECT_ROOT/skills 不在发现根
curator 死代码: 仅测试调用, 根不重叠
```

## 重构后（统一架构）

### 核心组件

```
SkillRegistry (单例, floodmind/skills/registry.py)
│
├── roots: List[Path]           # CWD 无关（_PROJECT_ROOT 包定位）
│   ├── floodmind/skills/       # 内置技能
│   ├── PROJECT_ROOT/skills/    # 项目/用户技能 (writable_root)
│   └── PROJECT_ROOT/.claude/skills/  # Claude Code 兼容
│
├── writable_root: Path         # CreateSkill 落盘目标
│
├── _scan()                     # 重扫所有根
│   ├── discover_skills_from_roots → _parse_skill_md (含威胁扫描)
│   ├── 保留 ephemeral (无 skill_dir 的编程式 skill)
│   ├── 过滤 disabled 集合
│   └── generate_skill_catalog → self._catalog (单 catalog)
│
├── refresh() → List[Skill]    # 重扫 + _notify_changed 回调
├── register_skill(skill)       # 编程式注册 (去重)
├── set_disabled(name, bool)    # 禁用/启用
├── list_skills() → List[dict]  # name/version/category/source/disabled
├── get_skill(name) → Skill|None
├── catalog() → str             # Markdown 格式目录 (进 system prompt)
│
└── _refresh_callbacks          # 变更通知
    └── _get_skill_cached.cache_clear  # 清 GetSkill lru_cache

SkillCurator (单例, floodmind/skills/skill_curator.py)
│
├── skills_dirs → SkillRegistry.roots   # 统一发现源
├── _primary_root → writable_root       # 写/恢复根
├── archive_root → .archived/           # 归档根
│
├── record_usage(name, success)         # 每次 GetSkill 触发
│   ├── 首次: 创建 SkillStat(active)
│   ├── 累计 total_uses/success/failure
│   └── 自动 reactivate stale/archived
│
├── archive_skill(name) → bool          # 移到 archive_root
├── restore_skill(name) → bool          # 恢复到 _primary_root
├── find_stale_skills()                 # days >= 30
├── find_duplicates(threshold)          # Jaccard bigram >= 0.7
├── run_maintenance() → dict            # stale→archive→duplicates
│
└── run_maintenance_if_needed()         # 模块级函数，6h 间隔
    └── 标记文件 .last_skill_maintenance
```

### 数据流

```
┌─ 发现 ───────────────────────────────────────────────────────────┐
│                                                                    │
│  roots → discover_skills_from_roots → _parse_skill_md             │
│           │                │           ├─ YAML frontmatter         │
│           │                │           ├─ body limit (500000 chars)│
│           │                │           ├─ 威胁扫描 (body)          │
│           │                │           └─ → Skill obj             │
│           │                └─ 合并 ephemeral (编程式)              │
│           │                └─ 过滤 disabled                       │
│           │                └─ generate_skill_catalog              │
│           └────────────────────────────────────────────────────── │
│                                                                    │
├─ 消费 ───────────────────────────────────────────────────────────┤
│                                                                    │
│  catalog() → NATIVE SYSTEM PROMPT (STATIC_GLOBAL)                  │
│    "## 可用 skills\n{skill_catalog}"                              │
│                                                                    │
│  GetSkill(name)                                                    │
│    → _get_skill_cached [@lru_cache]                               │
│      → _find_skill → registry.get_skill(name)                     │
│      → 拼装 Markdown (触发条件/说明/脚本/路径/参考/资源)           │
│      → record_skill_usage(name, success)                          │
│                                                                    │
│  registry 变更 → add_refresh_callback → cache_clear               │
│                                                                    │
├─ CRUD ───────────────────────────────────────────────────────────┤
│                                                                    │
│  CreateSkill(name, desc, body)                                     │
│     → writable_root/name/SKILL.md → refresh_skills()              │
│                                                                    │
│  UpdateSkill(name, action, content, section_title)                 │
│     → _resolve_skill_md_path → 读 → 改节 → 写 → refresh_skills()  │
│     action: append | replace_body | replace_section | remove_section│
│                                                                    │
│  RemoveSkill(name)                                                 │
│     → 编程式: set_disabled(name, True)                             │
│     → 落盘: curator.archive_skill(name) → .archived/              │
│     → refresh_skills()                                             │
│                                                                    │
│  ListSkills() → registry.list_skills()                            │
│  RefreshSkills() → registry.refresh() + _rebuild_system_prompts   │
│                                                                    │
├─ Curator ────────────────────────────────────────────────────────┤
│                                                                    │
│  GetSkill 每次调用 → record_skill_usage                            │
│  定期巡检 (6h) → run_maintenance_if_needed()                      │
│    → 标记 stale: active + 30d 未使用                              │
│    → 归档: stale + 90d 未使用 → archive_skill                     │
│    → 重复检测: Jaccard similarity                                 │
│                                                                    │
│  restore_skill(name) → .archived/ → writable_root                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### refresh_skills() 统一链路

```
refresh_skills()
  ├── get_skill_registry().refresh()
  │     └── _scan() → 重扫 roots
  │     └── _notify_changed() → cache_clear
  ├── self._skill_catalog = get_skill_registry().catalog()
  └── _rebuild_system_prompts()
        ├── orchestrator STATIC_GLOBAL 重建
        └── specialist STATIC_GLOBAL 重建

触发点:
  - _init_tools() 初始化
  - _handle_create_skill() 创建后
  - _handle_update_skill() 更新后
  - _handle_remove_skill() 移除后
  - _handle_refresh_skills() Agent 工具
  - _te._on_skill_generated callback (auto-gen 闭环)
```

### Agent CRUD 工具

全部 `state_write`（除 `ListSkills=readonly`），仅注册到 `_orchestrator_registry`。

| 工具 | policy | 路径防护 |
|---|---|---|
| `ListSkills` | readonly | N/A |
| `CreateSkill` | state_write | `_validate_skill_name` |
| `UpdateSkill` | state_write | `_validate_skill_name` |
| `RemoveSkill` | state_write+destructive | `_validate_skill_name` |
| `RefreshSkills` | state_write | N/A |

### 安全

- **路径穿越防护**: `_validate_skill_name` 拒绝 `/`, `\\`, `..`, 以 `.` 开头
- **威胁扫描**: `_parse_skill_md` 调用 `scan_content_threats` 扫描 body 文本
- **归档非硬删**: `archive_skill` 只做 `shutil.move` 到 `.archived/`

### 线程安全

- `SkillRegistry._lock`: `threading.Lock`
- `SkillCurator._lock`: `threading.RLock`（支持 `run_maintenance` → `archive_skill` 重入）
- 回调 `_notify_changed` 在锁外执行（避免回调锁与 registry 锁嵌套）

## 关键文件

| 文件 | 职责 |
|---|---|
| `floodmind/skills/base.py` | Skill dataclass, `_parse_skill_md`, `discover_skills/_from_roots`, `generate_skill_catalog` |
| `floodmind/skills/registry.py` | SkillRegistry 单例 |
| `floodmind/skills/skill_curator.py` | SkillCurator + `run_maintenance_if_needed` |
| `floodmind/skills/__init__.py` | `__getattr__` 向后兼容 (SKILL_REGISTRY, SKILL_CATALOG) |
| `floodmind/agent/native/native_flood_agent.py` | 5 CRUD handler + `refresh_skills` + 注册 |
| `floodmind/tools/base_tools.py` | GetSkill, `_get_skill_cached`, `record_skill_usage` |
| `tests/test_skill_registry.py` | SkillRegistry + CRUD handler 测试 (9 tests) |
| `tests/test_skill_curator.py` | Curator 测试 (17 tests) |
