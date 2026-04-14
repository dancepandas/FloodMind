"""
RAG (Retrieval-Augmented Generation) 模块

提供向量检索能力，支持从知识库中检索相关参考资料。
"""

from rag.embeddings import EmbeddingManager
from rag.vector_store import VectorStoreManager
from rag.retriever import KnowledgeRetriever

__all__ = [
    'EmbeddingManager',
    'VectorStoreManager', 
    'KnowledgeRetriever',
]
