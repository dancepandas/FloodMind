"""
全局配置管理模块

配置优先级 (高到低):
  1. 环境变量 (FLOODMIND_*)
  2. ~/.floodmind/settings.json (用户级 JSON)
  3. 内置默认值 (settings_template.json)

参照 OpenCode 的单文件配置风格。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from floodmind.config.secret_store import protect_tree, unprotect_tree

_logger = logging.getLogger(__name__)


# Network policy belongs on individual HTTP clients. Importing configuration must
# never alter process-wide DNS resolution or TLS verification.


# ── Config Path ────────────────────────────────────────────

_DEFAULT_HOME = Path.home() / ".floodmind"
_PROFILES_ROOT = Path.home() / ".floodmind" / "profiles"

# 活跃 Profile 缓存
_active_profile_cache: Optional[str] = None


def get_active_profile() -> str:
    """读取 ~/.floodmind/active_profile，返回 profile 名称或 'default'。"""
    global _active_profile_cache
    if _active_profile_cache is not None:
        return _active_profile_cache
    env_profile = os.getenv("FLOODMIND_PROFILE", "").strip()
    if env_profile:
        _active_profile_cache = env_profile
        return _active_profile_cache
    active_file = _DEFAULT_HOME / "active_profile"
    if active_file.exists():
        try:
            name = active_file.read_text(encoding="utf-8").strip()
            if name and name != "default":
                _active_profile_cache = name
                return _active_profile_cache
        except Exception:
            pass
    _active_profile_cache = "default"
    return "default"


def set_active_profile(name: str) -> None:
    """设置活跃 profile，写入 active_profile 文件。"""
    global _active_profile_cache
    _active_profile_cache = None
    _DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
    active_file = _DEFAULT_HOME / "active_profile"
    if name == "default":
        if active_file.exists():
            active_file.unlink()
    else:
        active_file.write_text(name, encoding="utf-8")


def get_floodmind_home() -> Path:
    """返回当前生效的 FloodMind 根目录。

    优先级：
    1. FLOODMIND_HOME 环境变量
    2. 活跃 profile 目录
    3. ~/.floodmind/ (默认)
    """
    env_home = os.getenv("FLOODMIND_HOME", "").strip()
    if env_home:
        return Path(env_home)

    profile = get_active_profile()
    if profile and profile != "default":
        profile_dir = _PROFILES_ROOT / profile
        if profile_dir.is_dir():
            return profile_dir

    return _DEFAULT_HOME


def _config_path() -> Path:
    return get_floodmind_home() / "settings.json"


def _template_path() -> Path:
    return Path(__file__).parent / "settings_template.json"


def initialize_floodmind_home() -> Path:
    """Create the configured FloodMind home and seed settings when absent.

    This is an explicit runtime operation. Importing :mod:`floodmind.config`
    never creates directories or copies configuration files.
    """
    home = get_floodmind_home()
    home.mkdir(parents=True, exist_ok=True)
    user_path = home / "settings.json"
    if not user_path.exists():
        template_path = _template_path()
        if template_path.exists():
            user_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
            _logger.info("已创建初始配置: %s (请编辑此文件配置 API 密钥)", user_path)
    return home


def migrate_settings() -> bool:
    """Persist legacy settings and MCP layouts in the current FloodMind home.

    Returns ``True`` when any file changed. The operation is idempotent and is
    deliberately separate from imports and ordinary read-only config loading.
    """
    user_path = _config_path()
    user_cfg = _load_json_config(user_path)
    next_cfg, migrated = _migrate_legacy_config(user_cfg)
    legacy_servers = next_cfg.pop("mcpServers", next_cfg.pop("mcp_servers", []))
    mcp_path = _mcp_config_path()
    migrate_mcp = bool(
        legacy_servers and isinstance(legacy_servers, list) and not mcp_path.exists()
    )
    if not migrated and not migrate_mcp:
        return False

    initialize_floodmind_home()
    if user_path.exists():
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = user_path.with_name(f"settings.json.bak.{stamp}")
        backup.write_text(user_path.read_text(encoding="utf-8"), encoding="utf-8")
        _logger.info("已备份旧 settings.json: %s", backup)

    user_path.write_text(
        json.dumps(protect_tree(next_cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if migrate_mcp:
        mcp_path.write_text(
            json.dumps({"servers": legacy_servers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.info("已将 mcpServers 从 settings.json 迁移到 mcp.json")

    global _config_cache
    _config_cache = None
    return True


# ── Built-in Defaults ──────────────────────────────────────

# Agent 循环上限：代码默认，不入配置（有 auto-compact + DOOM LOOP 检测兜底）。
DEFAULT_MAX_ITERATIONS = 999

# settings.json 仅暴露 providers（服务商目录）；其余子系统参数为代码内部默认，
# 高级用户可手动追加覆盖。模型生成参数（temperature/max_tokens/context_window）
# 只挂在模型自身定义上，顶层不重复——单一真相源。
DEFAULT_CONFIG: Dict[str, Any] = {
    "providers": {},
    "task_experience": {
        "persist_dir": "./data/task_experience",
        "seal_threshold": 5,
        "archive_after_days": 90,
        "skill_generation_threshold": 5,
    },
    "background_review": {
        "enabled": True,
        "min_message_count": 3,
    },
    "memory_recall": {
        "enabled": True,
        "top_k": 5,
        "min_score": 0.05,
        "decay_scale_days": 30,
        "max_chars": 1500,
    },
    "memory_consolidation": {
        "enabled": True,
        "conflict_candidate_top_k": 3,
    },
    "core_memory": {
        "enabled": True,
        "max_chars": 800,
        "migrate_legacy": True,
    },
    "tool_loading": {
        "mode": "progressive",
        "core_tools": ["GetTool", "GetSkill"],
        "max_search_results": 8,
        "max_loaded_tools": 12,
        "get_tool_loads_tool": True,
    },
    "model_routing": {
        "subagent_model": "",
        "fallbacks": [],
        "max_fallbacks": 2,
    },
    "model_pricing": {},
    "observability": {
        "otlp_endpoint": "",
        "otlp_headers": {},
        "otlp_timeout_seconds": 5.0,
    },
    "api": {
        "base_url": "http://127.0.0.1:8000",
        "timeout": 60,
    },
    "workspace": {
        "default_user_dir": "",
        "session_root": "",
        "sandbox_strategy": "session_root",
        "overwrite_protection": False,
    },
}


# ── Config Loading ─────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 覆盖 base。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_json_config(path: Path) -> dict:
    """加载 JSON 配置文件。"""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _logger.warning("加载配置文件失败 %s: %s", path, e)
        return {}


def _rename_keys(d: dict, mapping: dict) -> dict:
    """按 mapping 把 camelCase 键改名为 snake_case（仅对存在的键）。"""
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        out[mapping.get(k, k)] = v
    return out


def _migrate_legacy_config(cfg: dict) -> tuple:
    """把旧格式 settings.json 归一化为新格式（仅 providers 目录）。

    返回 (新cfg, 是否发生迁移)。幂等：新格式输入不触发任何改动。

    - provider（单数）→ providers；options.{apiKey,baseURL} 扁平化；models dict → list
    - model 选择段、agent.maxHistory/contextWindow/enableChronosWarmup、
      task_experience.enabled/autoCapture → 丢弃（已有代码默认/会话级替代）
    - task_experience/workspace 残留 camelCase → snake_case
    """
    from floodmind.config.model_resolver import _normalize_models

    migrated = False
    out = dict(cfg)

    # 1) provider(单数) → providers
    if "provider" in out:
        legacy = out.pop("provider")
        if "providers" not in out and isinstance(legacy, dict):
            from floodmind.config.model_resolver import _migrate_legacy_providers
            out["providers"] = _migrate_legacy_providers(legacy)
        migrated = True

    # 归一化 providers 内部残留的 options / dict-models
    if isinstance(out.get("providers"), dict):
        new_prov = {}
        for pid, pdata in out["providers"].items():
            if not isinstance(pdata, dict):
                new_prov[pid] = pdata
                continue
            np = dict(pdata)
            if "options" in np:
                opts = np.pop("options")
                if isinstance(opts, dict):
                    np.setdefault("api_key", opts.get("apiKey") or opts.get("api_key", ""))
                    np.setdefault("base_url", opts.get("baseURL") or opts.get("base_url", ""))
                    migrated = True
            if isinstance(np.get("models"), dict):
                np["models"] = _normalize_models(np["models"])
                migrated = True
            new_prov[pid] = np
        out["providers"] = new_prov

    # 2) 丢弃旧 model 选择段
    if "model" in out:
        out.pop("model", None)
        migrated = True

    # 3) agent 段：移除已废弃键；空则整段删除
    if isinstance(out.get("agent"), dict) and out["agent"]:
        agent = dict(out["agent"])
        stale = ("maxHistory", "contextWindow", "enableChronosWarmup")
        if any(k in agent for k in stale):
            agent = {k: v for k, v in agent.items() if k not in stale}
            migrated = True
        out["agent"] = agent or None
        if out["agent"] is None:
            out.pop("agent", None)

    # 4) task_experience：去开关 + camelCase→snake_case
    if isinstance(out.get("task_experience"), dict):
        te = out["task_experience"]
        for stale in ("enabled", "autoCapture"):
            if stale in te:
                te.pop(stale, None)
                migrated = True
        te = _rename_keys(te, {
            "persistDir": "persist_dir",
            "sealThreshold": "seal_threshold",
            "archiveAfterDays": "archive_after_days",
            "skillGenerationThreshold": "skill_generation_threshold",
            "hotnessDecayDays": "hotness_decay_days",
            "maintenanceIntervalHours": "maintenance_interval_hours",
            "dedupSimilarityThreshold": "dedup_similarity_threshold",
            "minToolCalls": "min_tool_calls",
            "topK": "top_k",
        })
        out["task_experience"] = te

    # 5) workspace：camelCase→snake_case
    if isinstance(out.get("workspace"), dict):
        out["workspace"] = _rename_keys(out["workspace"], {
            "defaultUserDir": "default_user_dir",
            "sessionRoot": "session_root",
            "sandboxStrategy": "sandbox_strategy",
            "overwriteProtection": "overwrite_protection",
        })

    return out, migrated


def _load_config() -> dict:
    """Load defaults and existing configuration without writing to disk."""
    cfg = dict(DEFAULT_CONFIG)

    user_path = _config_path()
    user_cfg = _load_json_config(user_path)
    if user_cfg:
        normalized, _ = _migrate_legacy_config(user_cfg)
        cfg = _deep_merge(cfg, normalized)
        _logger.debug("已加载用户配置: %s", user_path)
    elif not user_path.exists():
        # Preserve the historical effective defaults without seeding HOME.
        template_cfg = _load_json_config(_template_path())
        if template_cfg:
            cfg = _deep_merge(cfg, template_cfg)

    old_path = Path.home() / ".config" / "floodmind" / "config.json"
    old_cfg = _load_json_config(old_path)
    if old_cfg:
        normalized, _ = _migrate_legacy_config(old_cfg)
        cfg = _deep_merge(cfg, normalized)
        _logger.debug("已加载旧配置: %s", old_path)

    # G-06：磁盘上是密文，内存中始终是明文；解密失败按空值处理（fail-closed）。
    return unprotect_tree(cfg)


# 全局配置缓存
_config_cache: Optional[dict] = None


def get_config() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = _load_config()
    return _config_cache


def reload_config() -> dict:
    """强制重新加载配置（用于运行时刷新）"""
    global _config_cache
    _config_cache = _load_config()
    return _config_cache


def _atomic_write_json(path, cfg: dict) -> None:
    """tmp + fsync + os.replace 原子写（P2-8）：崩溃不留半写文件——
    半写 settings.json 会在下次加载时静默回退为空配置，providers 全部"丢失"。"""
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def save_config(cfg: dict) -> None:
    """保存用户配置到 ~/.floodmind/settings.json（敏感值加密后落盘）。"""
    global _config_cache
    config_dir = get_floodmind_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path()
    _atomic_write_json(config_path, protect_tree(cfg))
    _config_cache = None  # 强制下次重新加载
    _logger.info("配置已保存: %s", config_path)


# ── Config Access Helper ───────────────────────────────────

def _cfg(json_cfg: dict, key_path: str, env_var: str, default: Any) -> Any:
    """按优先级取值: 环境变量 > JSON 配置 > 默认值"""
    env_val = os.getenv(env_var)
    if env_val is not None and env_val != "":
        return env_val

    keys = key_path.split(".")
    val = json_cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            val = None
        if val is None:
            return default
    return val if val is not None else default


# ── Config Classes ─────────────────────────────────────────

class ProviderConfig:
    """单个 Provider 的连接配置"""

    def __init__(self, name: str, data: dict):
        self.name = name
        data = data if isinstance(data, dict) else {}
        opts = data.get("options")
        opts = opts if isinstance(opts, dict) else {}
        # 新结构扁平 api_key/base_url；兼容旧 options.apiKey/baseURL
        self.api_key: str = (
            data.get("api_key") or data.get("apiKey") or opts.get("apiKey") or opts.get("api_key", "")
        )
        self.base_url: str = (
            data.get("base_url")
            or data.get("baseURL")
            or opts.get("baseURL")
            or opts.get("base_url", "https://api.openai.com/v1")
        )

    def __repr__(self):
        return f"Provider({self.name}, base_url={self.base_url})"


class ModelConfig:
    """激活模型配置——resolve_model() 的门面。

    保留 ``settings.model.*`` 调用点不破：所有属性委托 resolve_model()，
    使其成为唯一真相源。``model_name`` 支持 setter（CLI/TUI 切换模型用），
    设置后即作为 override；其余参数随 override 模型解析。
    模型生成参数（temperature/max_tokens/context_window）只来自模型自身定义。
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg
        # CLI/TUI 运行期切换 / 环境变量覆盖激活模型
        env_model = os.getenv("FLOODMIND_MODEL", "").strip()
        self._active_key: Optional[str] = env_model or None
        # 会话级运行时开关（默认 False，由 UI/SDK 传入；保留可写属性兼容旧调用）
        self.enable_reasoning = str(
            os.getenv("FLOODMIND_ENABLE_REASONING", "false")
        ).lower() == "true"
        self.enable_search = str(
            os.getenv("FLOODMIND_ENABLE_SEARCH", "false")
        ).lower() == "true"

    def _resolved(self):
        from floodmind.config.model_resolver import resolve_model
        return resolve_model(model_key=self._active_key)

    @property
    def provider_name(self) -> str:
        try:
            return self._resolved().provider
        except ValueError:
            return os.getenv("FLOODMIND_PROVIDER", "dashscope") or "dashscope"

    @property
    def model_name(self) -> str:
        # 反映"意图激活"的模型 key（env/override）；缺失才回退解析出的第一个
        if self._active_key:
            return self._active_key
        try:
            return self._resolved().id
        except ValueError:
            return os.getenv("FLOODMIND_MODEL", "") or "deepseek-v4-flash"

    @model_name.setter
    def model_name(self, key: str) -> None:
        self._active_key = key or None

    @property
    def api_key(self) -> str:
        try:
            return self._resolved().api_key
        except ValueError:
            return os.getenv("FLOODMIND_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")

    @property
    def base_url(self) -> str:
        try:
            return self._resolved().base_url
        except ValueError:
            return os.getenv("FLOODMIND_BASE_URL", "https://api.openai.com/v1")

    @property
    def temperature(self) -> float:
        try:
            return self._resolved().temperature
        except ValueError:
            return 0.3

    @property
    def max_tokens(self) -> int:
        try:
            return self._resolved().max_tokens
        except ValueError:
            return 8192

    @property
    def top_p(self) -> float:
        return 0.9

    @property
    def context_window(self) -> int:
        """记忆窗口——直接取自激活模型，无额外配置回退。"""
        try:
            return self._resolved().context_window
        except ValueError:
            return 32768

    @property
    def reasoning_model(self) -> str:
        return self.model_name

    def get_provider(self) -> "ProviderConfig":
        return ProviderConfig(self.provider_name, self._provider_data())

    def _provider_data(self) -> dict:
        from floodmind.config.model_resolver import _providers_section
        prov = _providers_section(self._cfg)
        return prov.get(self.provider_name, {}) if isinstance(prov, dict) else {}

    def get_models_list(self) -> list:
        from floodmind.config.model_presets import get_models_list as _gml
        return _gml()


class AgentConfig:
    """Agent 运行时配置。

    chronos 已外置为 MCP（不再 warmup）；max_iterations 为代码默认（不入配置，
    auto-compact + DOOM LOOP 兜底）。此段在 settings.json 中通常不存在。
    """

    def __init__(self, cfg: dict):
        self.runtime = "native"
        self.max_iterations = DEFAULT_MAX_ITERATIONS




class TaskExperienceConfig:
    """任务经验系统——强制常开（不加开关），仅保留调优阈值。"""

    def __init__(self, cfg: dict):
        self.enabled = True          # 一直开启，不再可配置
        self.auto_capture = True     # 一直开启，不再可配置
        self.persist_dir = _cfg(cfg, "task_experience.persist_dir", "TASK_EXPERIENCE_PERSIST_DIR", "./data/task_experience")
        self.top_k = int(_cfg(cfg, "task_experience.top_k", "TASK_EXPERIENCE_TOP_K", 5))
        self.min_tool_calls_for_capture = int(_cfg(cfg, "task_experience.min_tool_calls", "TASK_EXPERIENCE_MIN_TOOL_CALLS", 2))
        self.seal_threshold = int(_cfg(cfg, "task_experience.seal_threshold", "TASK_EXPERIENCE_SEAL_THRESHOLD", 5))
        self.hotness_decay_days = int(_cfg(cfg, "task_experience.hotness_decay_days", "TASK_EXPERIENCE_HOTNESS_DECAY_DAYS", 90))
        self.maintenance_interval_hours = int(_cfg(cfg, "task_experience.maintenance_interval_hours", "TASK_EXPERIENCE_MAINTENANCE_INTERVAL_HOURS", 6))
        self.dedup_similarity_threshold = float(_cfg(cfg, "task_experience.dedup_similarity_threshold", "TASK_EXPERIENCE_DEDUP_THRESHOLD", 0.8))
        self.archive_after_days = int(_cfg(cfg, "task_experience.archive_after_days", "TASK_EXPERIENCE_ARCHIVE_AFTER_DAYS", 90))
        self.skill_generation_threshold = int(_cfg(cfg, "task_experience.skill_generation_threshold", "TASK_EXPERIENCE_SKILL_GEN_THRESHOLD", 5))


class BackgroundReviewConfig:
    def __init__(self, cfg: dict):
        raw = cfg.get("background_review", {})
        if not isinstance(raw, dict):
            raw = {}
        self.enabled = raw.get("enabled", True)
        self.min_message_count = int(raw.get("min_message_count", 3))


def _env_bool(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class MemoryRecallConfig:
    """语义事实主动注入（P0-1）。"""

    def __init__(self, cfg: dict):
        self.enabled = _env_bool(_cfg(cfg, "memory_recall.enabled", "MEMORY_RECALL_ENABLED", True))
        self.top_k = int(_cfg(cfg, "memory_recall.top_k", "MEMORY_RECALL_TOP_K", 5))
        self.min_score = float(_cfg(cfg, "memory_recall.min_score", "MEMORY_RECALL_MIN_SCORE", 0.05))
        self.decay_scale_days = float(_cfg(cfg, "memory_recall.decay_scale_days", "MEMORY_RECALL_DECAY_DAYS", 30))
        self.max_chars = int(_cfg(cfg, "memory_recall.max_chars", "MEMORY_RECALL_MAX_CHARS", 1500))


class MemoryConsolidationConfig:
    """四类操作固化管线（P0-2）。"""

    def __init__(self, cfg: dict):
        self.enabled = _env_bool(_cfg(cfg, "memory_consolidation.enabled", "MEMORY_CONSOLIDATION_ENABLED", True))
        self.conflict_candidate_top_k = int(_cfg(cfg, "memory_consolidation.conflict_candidate_top_k", "MEMORY_CONSOLIDATION_CANDIDATE_TOP_K", 3))


class CoreMemoryConfig:
    """项目级 core memory（P0-3）。"""

    def __init__(self, cfg: dict):
        self.enabled = _env_bool(_cfg(cfg, "core_memory.enabled", "CORE_MEMORY_ENABLED", True))
        self.max_chars = int(_cfg(cfg, "core_memory.max_chars", "CORE_MEMORY_MAX_CHARS", 800))
        self.migrate_legacy = _env_bool(_cfg(cfg, "core_memory.migrate_legacy", "CORE_MEMORY_MIGRATE_LEGACY", True))


class ToolLoadingConfig:
    def __init__(self, cfg: dict):
        raw = cfg.get("tool_loading", {})
        if not isinstance(raw, dict):
            raw = {}
        mode = str(raw.get("mode", "progressive") or "progressive").lower()
        self.mode = mode if mode in {"eager", "catalog", "progressive"} else "progressive"
        core = raw.get("core_tools", ["GetTool", "GetSkill"])
        self.core_tools = list(core) if isinstance(core, list) else ["GetTool", "GetSkill"]
        self.max_search_results = int(raw.get("max_search_results", 8) or 8)
        self.max_loaded_tools = int(raw.get("max_loaded_tools", 12) or 12)
        self.get_tool_loads_tool = bool(raw.get("get_tool_loads_tool", True))


# ── MCP 配置 (独立文件 mcp.json) ─────────────────────────

_MCP_TRANSPORTS = frozenset({"sse", "stdio"})
_MCP_SERVER_KEYS = frozenset({
    "name", "transport", "enabled", "url", "headers", "request_timeout",
    "command", "args", "env",
})


class McpConfigError(ValueError):
    """mcp.json 不符合唯一契约（字段名/传输类型/必填项）。不做兜底解析。"""


def _mcp_config_path() -> Path:
    return get_floodmind_home() / "mcp.json"


def _validate_mcp_servers(raw: Any, source: str) -> List[Dict[str, Any]]:
    """校验 servers 数组，返回原顺序的合法条目；任何违规直接抛 ``McpConfigError``。

    ``source`` 用于错误定位（配置文件路径或环境变量名）。
    """
    if not isinstance(raw, list):
        raise McpConfigError(f"{source}: servers 必须是数组")
    validated: List[Dict[str, Any]] = []
    seen_names: set = set()
    for index, entry in enumerate(raw):
        where = f"{source}: servers[{index}]"
        if not isinstance(entry, dict):
            raise McpConfigError(f"{where} 必须是对象")
        if "type" in entry:
            raise McpConfigError(
                f"{where} 包含不受支持的字段 'type'；传输类型字段名必须是 'transport'"
                f"（取值 {'/'.join(sorted(_MCP_TRANSPORTS))}）"
            )
        unknown = sorted(set(entry) - _MCP_SERVER_KEYS)
        if unknown:
            raise McpConfigError(
                f"{where} 包含未知字段: {', '.join(unknown)}；"
                f"允许字段: {', '.join(sorted(_MCP_SERVER_KEYS))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise McpConfigError(f"{where} 缺少非空 name")
        if name in seen_names:
            raise McpConfigError(f"{where} name 重复: {name}")
        seen_names.add(name)
        transport = entry.get("transport")
        if transport not in _MCP_TRANSPORTS:
            raise McpConfigError(
                f"{where} transport 必须是 {'/'.join(sorted(_MCP_TRANSPORTS))}，"
                f"收到: {transport!r}"
            )
        if transport == "sse" and not entry.get("url"):
            raise McpConfigError(f"{where} transport=sse 需要非空 url")
        if transport == "stdio" and not entry.get("command"):
            raise McpConfigError(f"{where} transport=stdio 需要非空 command")
        if "args" in entry and not isinstance(entry["args"], list):
            raise McpConfigError(f"{where} args 必须是数组")
        for key in ("env", "headers"):
            if key in entry and not isinstance(entry[key], dict):
                raise McpConfigError(f"{where} {key} 必须是对象")
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise McpConfigError(f"{where} enabled 必须是布尔值")
        validated.append(entry)
    return validated


def _warn_if_project_mcp_config_ignored(active_path: Path) -> None:
    """显式区分两份 mcp.json：项目级 ``.floodmind/mcp.json`` 不是运行配置。

    该文件通常来自旧版布局或手工拷贝，字段可能仍是 ``type``；静默忽略会让
    “改了配置不生效”极难排查，因此存在时每次加载都告警并给出正确路径。
    """
    project_path = Path.cwd() / ".floodmind" / "mcp.json"
    try:
        if not project_path.exists() or project_path.resolve() == active_path.resolve():
            return
    except OSError:
        return
    _logger.warning(
        "检测到项目级 MCP 配置 %s，该文件不会被加载；运行时只读取 %s。"
        "请把 servers 迁移到后者，并确认字段名为 transport（type 不受支持）。",
        project_path, active_path,
    )


def load_mcp_config() -> Dict[str, Any]:
    """加载并校验 MCP 配置（唯一契约；不做无效字段兜底）。

    只读取 ``get_floodmind_home()/mcp.json``（``MCP_SERVERS`` 环境变量可整体
    覆盖 servers）。条目不合规抛 ``McpConfigError``，由调用方在启动点显式报错。
    """
    mcp_path = _mcp_config_path()
    mcp_cfg: Dict[str, Any] = {}
    if mcp_path.exists():
        try:
            text = mcp_path.read_text(encoding="utf-8")
            parsed_file = json.loads(text) if text.strip() else {}
        except (OSError, ValueError) as exc:
            raise McpConfigError(f"{mcp_path} 不可读或不是合法 JSON: {exc}") from exc
        if not isinstance(parsed_file, dict):
            raise McpConfigError(f"{mcp_path} 顶层必须是对象")
        # G-06：headers/env 中的凭证在磁盘上加密，运行时还原为明文。
        mcp_cfg = unprotect_tree(parsed_file)

    if not mcp_cfg:
        mcp_cfg = {"servers": []}

    source = str(mcp_path)
    env_raw = os.getenv("MCP_SERVERS", "")
    if env_raw.strip():
        try:
            parsed = json.loads(env_raw)
        except ValueError as exc:
            raise McpConfigError(f"MCP_SERVERS 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise McpConfigError("MCP_SERVERS 必须是 JSON 数组")
        mcp_cfg["servers"] = parsed
        source = "MCP_SERVERS"

    mcp_cfg["servers"] = _validate_mcp_servers(mcp_cfg.get("servers", []), source)
    _warn_if_project_mcp_config_ignored(mcp_path)
    return mcp_cfg


def save_mcp_config(mcp_cfg: Dict[str, Any]) -> None:
    """校验后保存 MCP 配置到 mcp.json；非法条目拒绝落盘。"""
    servers = _validate_mcp_servers(mcp_cfg.get("servers", []), "save_mcp_config")
    config_dir = get_floodmind_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = _mcp_config_path()
    _atomic_write_json(mcp_path, protect_tree({**mcp_cfg, "servers": servers}))
    _logger.info("MCP 配置已保存: %s", mcp_path)


class McpServerConfig:
    """运行时 MCP server 快照。

    配置非法时设置 ``error`` 并拒绝暴露任何 server（fail-closed）；由 agent
    初始化点（``_load_mcp_tools``）抛出 ``McpConfigError``，避免带着空 MCP 静默运行。
    """

    def __init__(self):
        self.error: str = ""
        try:
            mcp_cfg = load_mcp_config()
        except McpConfigError as exc:
            self.error = str(exc)
            self.servers: List[Dict[str, Any]] = []
            _logger.error("MCP 配置无效，已拒绝接入任何 server: %s", exc)
            return
        self.servers = [
            entry for entry in mcp_cfg["servers"] if entry.get("enabled", True)
        ]


class APIConfig:
    def __init__(self, cfg: dict):
        self.base_url = _cfg(cfg, "api.base_url", "FLOOD_API_URL", "http://127.0.0.1:8000")
        self.timeout = int(_cfg(cfg, "api.timeout", "API_TIMEOUT", 60))


class WorkspaceConfig:
    """工作区配置：决定产物目录与沙盒布局，为桌面版铺路。

    默认值保持网页版行为：
    - defaultUserDir 为空 → build_workspace 回退到 session_root/<sid>/outputs
    - sessionRoot 为空 → build_workspace 回退到 PROJECT_ROOT/data/sessions
    - sandboxStrategy="session_root" → 子代理沙盒仍在 data/sessions 下（旧布局）
    - overwriteProtection=false → 允许覆盖（与现状一致）
    """

    def __init__(self, cfg: dict):
        self.default_user_dir = _cfg(cfg, "workspace.default_user_dir", "FLOODMIND_USER_DIR", "")
        self.session_root = _cfg(cfg, "workspace.session_root", "FLOODMIND_SESSION_ROOT", "")
        self.sandbox_strategy = _cfg(
            cfg, "workspace.sandbox_strategy", "FLOODMIND_SANDBOX_STRATEGY", "session_root"
        )
        ow = _cfg(cfg, "workspace.overwrite_protection", "FLOODMIND_OVERWRITE_PROTECTION", "false")
        self.overwrite_protection = str(ow).lower() == "true"


class Settings:
    """全局配置类"""

    def __init__(self):
        cfg = get_config()
        self.api = APIConfig(cfg)
        self.model = ModelConfig(cfg)
        self.agent = AgentConfig(cfg)
        self.task_experience = TaskExperienceConfig(cfg)
        self.background_review = BackgroundReviewConfig(cfg)
        self.memory_recall = MemoryRecallConfig(cfg)
        self.memory_consolidation = MemoryConsolidationConfig(cfg)
        self.core_memory = CoreMemoryConfig(cfg)
        self.tool_loading = ToolLoadingConfig(cfg)
        self.mcp = McpServerConfig()
        self.workspace = WorkspaceConfig(cfg)
        # 模型单价表（usage_accounting 成本归因）：{model: {"input_per_1k": float,
        # "output_per_1k": float, "currency": "USD"}}；空表表示不估算成本
        self.model_pricing = dict(cfg.get("model_pricing", {}))
        # OTLP/HTTP 导出配置（C4）：endpoint 为空表示只落本地 trace/metrics
        self.observability = dict(cfg.get("observability", {}))

    @property
    def qwen(self):
        """兼容旧代码: settings.qwen → settings.model"""
        return self.model


settings = Settings()
