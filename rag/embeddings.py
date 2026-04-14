"""
Embedding 模型封装

支持本地 HuggingFace 模型和在线 Embedding API。
"""

import logging
import os
from typing import List, Optional

# 必须先加载 .env，再导入 config.settings（settings 依赖环境变量）
from dotenv import load_dotenv
load_dotenv()

# 现在可以安全导入 settings，HF_ENDPOINT 等环境变量已配置
import config.settings  # noqa: F401

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_SMALL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_MODEL_BASE = "BAAI/bge-base-zh-v1.5"
EMBEDDING_MODEL_LARGE = "BAAI/bge-large-zh-v1.5"


class EmbeddingManager:
    """Embedding 模型管理器"""
    
    _instance: Optional['EmbeddingManager'] = None
    _embeddings: Optional[Embeddings] = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL_SMALL,
        device: str = "cpu",
        cache_folder: Optional[str] = None,
    ):
        if self._embeddings is not None:
            return
            
        self.model_name = model_name
        self.device = device
        self.cache_folder = cache_folder or os.getenv("HF_HOME")
        
        logger.info(f"初始化 Embedding 模型: {model_name}, 设备: {device}")
    
    def get_embeddings(self) -> Embeddings:
        """获取 Embeddings 实例（懒加载）"""
        if self._embeddings is None:
            self._embeddings = self._load_embeddings()
        return self._embeddings
    
    def _load_embeddings(self) -> Embeddings:
        """加载 Embedding 模型"""
        try:
            # 优先使用新版 langchain-huggingface（LangChain >= 0.2.2 推荐）
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError:
            # 降级到旧版兼容
            logger.warning("langchain-huggingface 未安装，使用旧版 langchain_community")
            from langchain_community.embeddings import HuggingFaceEmbeddings
        
        model_kwargs = {
            "device": self.device,
        }
        
        encode_kwargs = {
            "normalize_embeddings": True,
        }
        
        kwargs = {
            "model_name": self.model_name,
            "model_kwargs": model_kwargs,
            "encode_kwargs": encode_kwargs,
        }
        
        if self.cache_folder:
            kwargs["cache_folder"] = self.cache_folder
        
        embeddings = HuggingFaceEmbeddings(**kwargs)
        
        test_embedding = embeddings.embed_query("测试")
        logger.info(f"Embedding 模型加载成功，向量维度: {len(test_embedding)}")
        
        return embeddings
    
    def embed_query(self, text: str) -> List[float]:
        """将查询文本转换为向量"""
        return self.get_embeddings().embed_query(text)
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """将文档列表转换为向量列表"""
        return self.get_embeddings().embed_documents(texts)
    
    @classmethod
    def reset(cls):
        """重置单例（用于测试）"""
        cls._instance = None
        cls._embeddings = None
