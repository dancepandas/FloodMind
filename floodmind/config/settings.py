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

def _config_dir() -> Path:
    """XDG config directory: ~/.floodmind/"""
    return Path.home() / ".floodmind"


def _config_path() -> Path:
    return _config_dir() / "settings.json"


def _template_path() -> Path:
    return Path(__file__).parent / "settings_template.json"


# ── Built-in Defaults ──────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "agent": {
        "maxHistory": 20,
        "contextWindow": 32768,
        "enableChronosWarmup": False,
    },
    "rag": {
        "enabled": True,
        "persistDir": "./data/vector_store",
        "embeddingModel": "BAAI/bge-base-zh-v1.5",
        "topK": 10,
    },
    "task_experience": {
        "enabled": True,
        "autoCapture": True,
        "persistDir": "./data/task_experience",
        "sealThreshold": 5,
        "archiveAfterDays": 90,
        "skillGenerationThreshold": 5,
    },
    "mcpServers": [],
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
            _config_dir().mkdir(parents=True, exist_ok=True)
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

    # 兼容旧的 YAML 路径
    yaml_path = Path.home() / ".floodmind" / "settings.yaml"
    if yaml_path.exists():
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_cfg = yaml.safe_load(f)
            if isinstance(yaml_cfg, dict):
                cfg = _deep_merge(cfg, yaml_cfg)
                _logger.debug("已加载旧 YAML 配置: %s", yaml_path)
        except Exception:
            pass

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
    config_dir = _config_dir()
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


class RAGConfig:
    def __init__(self, cfg: dict):
        self.enabled = _cfg(cfg, "rag.enabled", "RAG_ENABLED", "true")
        if isinstance(self.enabled, str):
            self.enabled = self.enabled.lower() == "true"
        self.persist_dir = _cfg(cfg, "rag.persistDir", "RAG_PERSIST_DIR", "./data/vector_store")
        self.embedding_model = _cfg(cfg, "rag.embeddingModel", "RAG_EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")
        self.top_k = int(_cfg(cfg, "rag.topK", "RAG_TOP_K", 10))
        self.small_doc_threshold = int(_cfg(cfg, "rag.small_doc_threshold", "RAG_SMALL_DOC_THRESHOLD", 10000))


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


class McpServerConfig:
    def __init__(self, cfg: dict):
        raw = cfg.get("mcpServers", cfg.get("mcp_servers", []))
        if isinstance(raw, list):
            self.servers: List[Dict[str, Any]] = raw
        else:
            env_raw = os.getenv("MCP_SERVERS", "")
            import json as _json
            if env_raw.strip():
                try:
                    parsed = _json.loads(env_raw)
                    self.servers = parsed if isinstance(parsed, list) else []
                except Exception:
                    self.servers = []
            else:
                self.servers = []


class APIConfig:
    def __init__(self, cfg: dict):
        self.base_url = _cfg(cfg, "api.base_url", "FLOOD_API_URL", "http://127.0.0.1:8000")
        self.timeout = int(_cfg(cfg, "api.timeout", "API_TIMEOUT", 60))


class Settings:
    """全局配置类"""

    def __init__(self):
        cfg = get_config()
        self.api = APIConfig(cfg)
        self.model = ModelConfig(cfg)
        self.agent = AgentConfig(cfg)
        self.rag = RAGConfig(cfg)
        self.task_experience = TaskExperienceConfig(cfg)
        self.mcp = McpServerConfig(cfg)

    @property
    def qwen(self):
        """兼容旧代码: settings.qwen → settings.model"""
        return self.model


settings = Settings()
