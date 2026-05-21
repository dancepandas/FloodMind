"""
全局配置管理模块

管理 API 配置、Qwen 模型配置等全局设置。
"""

import os
import socket
import ssl
import logging

# 自动加载 .env 文件，无需在每个入口点手动调用 load_dotenv()
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_logger = logging.getLogger(__name__)

if os.getenv("PYTHON_IPV6", "0") != "1":
    _orig_getaddrinfo = socket.getaddrinfo
    def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _ipv4_only

if os.getenv("ALLOW_INSECURE_SSL", "0") == "1":
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        pass
    else:
        ssl._create_default_https_context = _create_unverified_https_context

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

_hf_home = os.getenv('HF_HOME')
if _hf_home:
    os.environ['HF_HOME'] = _hf_home
    os.makedirs(_hf_home, exist_ok=True)


class APIConfig:
    """洪水预测API配置"""
    
    def __init__(self):
        """初始化API配置"""
        self.base_url = os.getenv("FLOOD_API_URL", "http://127.0.0.1:8000")
        self.timeout = int(os.getenv("API_TIMEOUT", "60"))


class QwenConfig:
    """Qwen模型配置"""

    def __init__(self):
        """初始化Qwen配置"""
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("未设置DASHSCOPE_API_KEY环境变量")
        self.api_key: str = api_key

        # 推理模式配置
        self.enable_reasoning = os.getenv("QWEN_ENABLE_REASONING", "false").lower() == "true"

        # 根据推理模式选择默认模型
        if self.enable_reasoning:
            default_model = "qwen-plus"  # 推理模式使用更强的模型
            default_max_tokens = "4096"   # 推理需要更多token空间
            default_temperature = "0.1"    # 更低的温度获得更确定性输出
        else:
            default_model = "qwen3-flash"  # 默认使用快速模型
            default_max_tokens = "1536"
            default_temperature = "0.3"

        self.model_name = os.getenv("QWEN_MODEL", default_model)
        self.reasoning_model = os.getenv("QWEN_REASONING_MODEL", "qwen-plus")

        self.max_tokens = int(os.getenv("QWEN_MAX_TOKENS", default_max_tokens))
        self.temperature = float(os.getenv("QWEN_TEMPERATURE", default_temperature))
        self.top_p = float(os.getenv("QWEN_TOP_P", "0.9"))

        self.enable_search = os.getenv("QWEN_ENABLE_SEARCH", "false").lower() == "true"
class AgentConfig:
    """智能体配置"""
    
    def __init__(self):
        self.runtime = os.getenv("AGENT_RUNTIME", "native").lower()
        if self.runtime != "native":
            _logger.warning(f"AGENT_RUNTIME='{self.runtime}' is no longer supported, forcing 'native'")
            self.runtime = "native"
        self.enable_chronos_warmup = os.getenv("AGENT_ENABLE_CHRONOS_WARMUP", "false").lower() == "true"
        self.max_history = int(os.getenv("AGENT_MAX_HISTORY", "20"))
        self.context_window = int(os.getenv("AGENT_CONTEXT_WINDOW", "32768"))


class RAGConfig:
    """RAG 知识检索配置"""
    
    def __init__(self):
        self.enabled = os.getenv("RAG_ENABLED", "true").lower() == "true"
        self.persist_dir = os.getenv("RAG_PERSIST_DIR", "./data/vector_store")
        self.embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")
        self.top_k = int(os.getenv("RAG_TOP_K", "5"))
        self.small_doc_threshold = int(os.getenv("RAG_SMALL_DOC_THRESHOLD", "10000"))


class TaskExperienceConfig:
    """任务执行经验知识库配置"""

    def __init__(self):
        self.enabled = os.getenv("TASK_EXPERIENCE_ENABLED", "true").lower() == "true"
        self.auto_capture = os.getenv("TASK_EXPERIENCE_AUTO_CAPTURE", "true").lower() == "true"
        self.persist_dir = os.getenv("TASK_EXPERIENCE_PERSIST_DIR", "./data/task_experience")
        self.top_k = int(os.getenv("TASK_EXPERIENCE_TOP_K", "5"))
        self.min_tool_calls_for_capture = int(os.getenv("TASK_EXPERIENCE_MIN_TOOL_CALLS", "2"))
        self.seal_threshold = int(os.getenv("TASK_EXPERIENCE_SEAL_THRESHOLD", "5"))
        self.hotness_decay_days = int(os.getenv("TASK_EXPERIENCE_HOTNESS_DECAY_DAYS", "90"))
        self.maintenance_interval_hours = int(os.getenv("TASK_EXPERIENCE_MAINTENANCE_INTERVAL_HOURS", "6"))
        self.dedup_similarity_threshold = float(os.getenv("TASK_EXPERIENCE_DEDUP_THRESHOLD", "0.8"))
        self.archive_after_days = int(os.getenv("TASK_EXPERIENCE_ARCHIVE_AFTER_DAYS", "90"))


class Settings:
    """全局配置类"""

    def __init__(self):
        self.api = APIConfig()
        self.qwen = QwenConfig()
        self.agent = AgentConfig()
        self.rag = RAGConfig()
        self.task_experience = TaskExperienceConfig()


settings = Settings()
