"""
Embedding 模型封装

支持本地 HuggingFace 模型，使用 sentence-transformers 直接加载。
"""

import logging
import os
from typing import List, Optional

import floodmind.config.settings  # noqa: F401

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_SMALL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_MODEL_BASE = "BAAI/bge-base-zh-v1.5"
EMBEDDING_MODEL_LARGE = "BAAI/bge-large-zh-v1.5"


class EmbeddingManager:
    """Embedding 模型管理器"""

    _instance: Optional['EmbeddingManager'] = None
    _model: Optional[object] = None

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
        if self._model is not None:
            return

        self.model_name = model_name
        self.device = device
        self.cache_folder = cache_folder or os.getenv("HF_HOME")

        logger.info(f"初始化 Embedding 模型: {model_name}, 设备: {device}")

    def get_model(self):
        """获取 SentenceTransformer 实例（懒加载）"""
        if self._model is None:
            self._model = self._load_model()
        return self._model

    def _load_model(self):
        """加载 Embedding 模型"""
        from sentence_transformers import SentenceTransformer

        kwargs = {}
        if self.cache_folder:
            kwargs["cache_folder"] = self.cache_folder

        model = SentenceTransformer(
            self.model_name,
            device=self.device,
            **kwargs,
        )

        test_embedding = model.encode("测试", normalize_embeddings=True)
        logger.info(f"Embedding 模型加载成功，向量维度: {len(test_embedding)}")

        return model

    def get_embeddings(self):
        """获取 Embeddings 实例（返回 self，兼容旧接口）"""
        return self

    def embed_query(self, text: str) -> List[float]:
        """将查询文本转换为向量"""
        return self.get_model().encode(text, normalize_embeddings=True).tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """将文档列表转换为向量列表"""
        return self.get_model().encode(texts, normalize_embeddings=True).tolist()

    @classmethod
    def reset(cls):
        """重置单例（用于测试）"""
        cls._instance = None
        cls._model = None