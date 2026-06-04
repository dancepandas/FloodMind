"""
RAG (Retrieval-Augmented Generation) 模块

提供向量检索能力，支持从知识库中检索相关参考资料。
"""

from floodmind.rag.embeddings import EmbeddingManager
from floodmind.rag.vector_store import VectorStoreManager
from floodmind.rag.retriever import KnowledgeRetriever

__all__ = [
    'EmbeddingManager',
    'VectorStoreManager', 
    'KnowledgeRetriever',
]
