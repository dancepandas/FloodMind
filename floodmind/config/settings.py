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
import socket
import ssl
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)

# IPv4-only 模式
if os.getenv("PYTHON_IPV6", "0") != "1":
    _orig_getaddrinfo = socket.getaddrinfo
    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _ipv4_only

# SSL
if os.getenv("ALLOW_INSECURE_SSL", "0") == "1":
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        pass
    else:
        ssl._create_default_https_context = _create_unverified_https_context

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
_hf_home = os.getenv("HF_HOME")
if _hf_home:
    os.environ["HF_HOME"] = _hf_home
    os.makedirs(_hf_home, exist_ok=True)


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


# ── Built-in Defaults ──────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "agent": {
        "maxHistory": 20,
        "contextWindow": 32768,
        "enableChronosWarmup": False,
    },
    "task_experience": {
        "enabled": True,
        "autoCapture": True,
        "persistDir": "./data/task_experience",
        "sealThreshold": 5,
        "archiveAfterDays": 90,
        "skillGenerationThreshold": 5,
    },
    "background_review": {
        "enabled": True,
        "min_message_count": 3,
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


def _load_config() -> dict:
    """加载配置：内置模板 + 用户 JSON 配置合并。"""
    cfg = dict(DEFAULT_CONFIG)

    # 加载内置模板作为默认值
    template_path = _template_path()
    template_cfg = _load_json_config(template_path)
    if template_cfg:
        cfg = _deep_merge(cfg, template_cfg)

    # 用户级 JSON 配置
    user_path = _config_path()
    user_cfg = _load_json_config(user_path)
    if user_cfg:
        cfg = _deep_merge(cfg, user_cfg)
        _logger.debug("已加载用户配置: %s", user_path)
    else:
        # 首次启动：自动复制模板作为初始配置
        try:
            get_floodmind_home().mkdir(parents=True, exist_ok=True)
            with open(template_path, "r", encoding="utf-8") as src:
                with open(user_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            _logger.info("已创建初始配置: %s (请编辑此文件配置 API 密钥)", user_path)
        except Exception:
            _logger.debug("无法自动创建配置文件: %s", user_path)

    # 兼容旧的 config.json 路径
    old_path = Path.home() / ".config" / "floodmind" / "config.json"
    old_cfg = _load_json_config(old_path)
    if old_cfg:
        cfg = _deep_merge(cfg, old_cfg)
        _logger.debug("已加载旧配置: %s", old_path)

    return cfg


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


def save_config(cfg: dict) -> None:
    """保存用户配置到 ~/.floodmind/settings.json。"""
    global _config_cache
    config_dir = get_floodmind_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
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
        self.api_key: str = data.get("api_key", "") if isinstance(data, dict) else ""
        self.base_url: str = data.get("base_url", "https://api.openai.com/v1") if isinstance(data, dict) else "https://api.openai.com/v1"

    def __repr__(self):
        return f"Provider({self.name}, base_url={self.base_url})"


class ModelConfig:
    """模型选择配置 — 从 settings.json 的 provider 段读取"""

    def __init__(self, cfg: dict):
        self._cfg = cfg

        self.provider_name = _cfg(cfg, "model.provider", "FLOODMIND_PROVIDER", "dashscope")
        self.model_name = _cfg(cfg, "model.model", "FLOODMIND_MODEL", "deepseek-v4-flash")
        self.enable_reasoning = _cfg(cfg, "model.enableReasoning", "FLOODMIND_ENABLE_REASONING", "false")
        if isinstance(self.enable_reasoning, str):
            self.enable_reasoning = self.enable_reasoning.lower() == "true"
        self.enable_search = _cfg(cfg, "model.enableSearch", "FLOODMIND_ENABLE_SEARCH", "false")
        if isinstance(self.enable_search, str):
            self.enable_search = self.enable_search.lower() == "true"

        self.temperature = float(_cfg(cfg, "model.temperature", "FLOODMIND_TEMPERATURE", 0.3))
        self.max_tokens = int(_cfg(cfg, "model.maxTokens", "FLOODMIND_MAX_TOKENS", 8192))
        self.top_p = float(_cfg(cfg, "model.topP", "FLOODMIND_TOP_P", 0.9))

        provider_data = self._get_provider(cfg)
        self.api_key = _cfg(cfg, "", "FLOODMIND_API_KEY", provider_data.get("apiKey", "") or provider_data.get("api_key", ""))
        if not self.api_key:
            self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = _cfg(cfg, "", "FLOODMIND_BASE_URL",
                             provider_data.get("baseURL", "") or provider_data.get("base_url", "https://api.openai.com/v1"))

        self.reasoning_model = self.model_name

    def _get_provider(self, cfg: dict) -> dict:
        provider_cfg = cfg.get("provider", {})
        if isinstance(provider_cfg, dict):
            prov = provider_cfg.get(self.provider_name, {})
            if isinstance(prov, dict):
                return prov.get("options", prov)
        return {}

    def get_provider(self) -> ProviderConfig:
        data = self._get_provider(self._cfg)
        return ProviderConfig(self.provider_name, data)

    def get_models_list(self) -> list:
        """从 provider 配置获取模型列表"""
        provider_cfg = self._cfg.get("provider", {})
        prov = provider_cfg.get(self.provider_name, {})
        models = prov.get("models", {}) if isinstance(prov, dict) else {}
        result = []
        for key, info in models.items():
            if isinstance(info, dict):
                result.append({
                    "key": key,
                    "label": info.get("name", key),
                    "description": info.get("description", ""),
                    "supportsReasoning": info.get("supportsReasoning", False),
                    "supportsVision": info.get("supportsVision", False),
                    "maxTokens": info.get("maxTokens", 8192),
                    "temperature": info.get("temperature", 0.3),
                })
        return result


class AgentConfig:
    def __init__(self, cfg: dict):
        self.runtime = "native"
        self.enable_chronos_warmup = _cfg(cfg, "agent.enableChronosWarmup", "AGENT_ENABLE_CHRONOS_WARMUP", "false")
        if isinstance(self.enable_chronos_warmup, str):
            self.enable_chronos_warmup = self.enable_chronos_warmup.lower() == "true"
        self.max_history = int(_cfg(cfg, "agent.maxHistory", "AGENT_MAX_HISTORY", 20))
        self.context_window = int(_cfg(cfg, "agent.contextWindow", "AGENT_CONTEXT_WINDOW", 32768))




class TaskExperienceConfig:
    def __init__(self, cfg: dict):
        self.enabled = _cfg(cfg, "task_experience.enabled", "TASK_EXPERIENCE_ENABLED", "true")
        if isinstance(self.enabled, str):
            self.enabled = self.enabled.lower() == "true"
        self.auto_capture = _cfg(cfg, "task_experience.autoCapture", "TASK_EXPERIENCE_AUTO_CAPTURE", "true")
        if isinstance(self.auto_capture, str):
            self.auto_capture = self.auto_capture.lower() == "true"
        self.persist_dir = _cfg(cfg, "task_experience.persistDir", "TASK_EXPERIENCE_PERSIST_DIR", "./data/task_experience")
        self.top_k = int(_cfg(cfg, "task_experience.topK", "TASK_EXPERIENCE_TOP_K", 5))
        self.min_tool_calls_for_capture = int(_cfg(cfg, "task_experience.minToolCalls", "TASK_EXPERIENCE_MIN_TOOL_CALLS", 2))
        self.seal_threshold = int(_cfg(cfg, "task_experience.sealThreshold", "TASK_EXPERIENCE_SEAL_THRESHOLD", 5))
        self.hotness_decay_days = int(_cfg(cfg, "task_experience.hotnessDecayDays", "TASK_EXPERIENCE_HOTNESS_DECAY_DAYS", 90))
        self.maintenance_interval_hours = int(_cfg(cfg, "task_experience.maintenanceIntervalHours", "TASK_EXPERIENCE_MAINTENANCE_INTERVAL_HOURS", 6))
        self.dedup_similarity_threshold = float(_cfg(cfg, "task_experience.dedupSimilarityThreshold", "TASK_EXPERIENCE_DEDUP_THRESHOLD", 0.8))
        self.archive_after_days = int(_cfg(cfg, "task_experience.archiveAfterDays", "TASK_EXPERIENCE_ARCHIVE_AFTER_DAYS", 90))
        self.skill_generation_threshold = int(_cfg(cfg, "task_experience.skillGenerationThreshold", "TASK_EXPERIENCE_SKILL_GEN_THRESHOLD", 5))


class BackgroundReviewConfig:
    def __init__(self, cfg: dict):
        raw = cfg.get("background_review", {})
        if not isinstance(raw, dict):
            raw = {}
        self.enabled = raw.get("enabled", True)
        self.min_message_count = int(raw.get("min_message_count", 3))


# ── MCP 配置 (独立文件 mcp.json) ─────────────────────────

def _mcp_config_path() -> Path:
    return get_floodmind_home() / "mcp.json"


def load_mcp_config() -> Dict[str, Any]:
    """加载 MCP 配置：mcp.json + 环境变量覆盖。

    首次启动时自动从 settings.json 的 mcpServers 字段迁移。
    """
    mcp_path = _mcp_config_path()

    # 加载 mcp.json
    if mcp_path.exists():
        mcp_cfg = _load_json_config(mcp_path)
    else:
        mcp_cfg = {}

    # 首次迁移：从 settings.json 的 mcpServers 字段迁移到 mcp.json
    if not mcp_cfg.get("servers") and mcp_path == get_floodmind_home() / "mcp.json":
        user_cfg = _load_json_config(_config_path())
        legacy = user_cfg.get("mcpServers", user_cfg.get("mcp_servers", []))
        if legacy and isinstance(legacy, list):
            mcp_cfg = {"servers": legacy}
            # 从 settings.json 中移除旧字段
            if "mcpServers" in user_cfg or "mcp_servers" in user_cfg:
                user_cfg.pop("mcpServers", None)
                user_cfg.pop("mcp_servers", None)
                save_config(user_cfg)
                _logger.info("已将 mcpServers 从 settings.json 迁移到 mcp.json")
            # 写入 mcp.json
            try:
                get_floodmind_home().mkdir(parents=True, exist_ok=True)
                with open(mcp_path, "w", encoding="utf-8") as f:
                    json.dump(mcp_cfg, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _logger.warning("写入 mcp.json 失败: %s", e)

    if not mcp_cfg:
        mcp_cfg = {"servers": []}

    # 环境变量覆盖
    env_raw = os.getenv("MCP_SERVERS", "")
    if env_raw.strip():
        try:
            parsed = json.loads(env_raw)
            if isinstance(parsed, list):
                mcp_cfg["servers"] = parsed
        except Exception:
            pass

    return mcp_cfg


def save_mcp_config(mcp_cfg: Dict[str, Any]) -> None:
    """保存 MCP 配置到 mcp.json。"""
    config_dir = get_floodmind_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = _mcp_config_path()
    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(mcp_cfg, f, ensure_ascii=False, indent=2)
    _logger.info("MCP 配置已保存: %s", mcp_path)


class McpServerConfig:
    def __init__(self):
        mcp_cfg = load_mcp_config()
        raw = mcp_cfg.get("servers", [])
        if isinstance(raw, list):
            self.servers: List[Dict[str, Any]] = raw
        else:
            self.servers = []


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
        self.default_user_dir = _cfg(cfg, "workspace.defaultUserDir", "FLOODMIND_USER_DIR", "")
        self.session_root = _cfg(cfg, "workspace.sessionRoot", "FLOODMIND_SESSION_ROOT", "")
        self.sandbox_strategy = _cfg(
            cfg, "workspace.sandboxStrategy", "FLOODMIND_SANDBOX_STRATEGY", "session_root"
        )
        ow = _cfg(cfg, "workspace.overwriteProtection", "FLOODMIND_OVERWRITE_PROTECTION", "false")
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
        self.mcp = McpServerConfig()
        self.workspace = WorkspaceConfig(cfg)

    @property
    def qwen(self):
        """兼容旧代码: settings.qwen → settings.model"""
        return self.model


settings = Settings()
