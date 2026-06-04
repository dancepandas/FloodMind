"""
知识检索器

结合向量检索与重排序，提供高质量的知识检索能力。
"""

import logging
from typing import Any, Dict, List, Optional

from floodmind.agent.runtime.contracts.messages import Document
from floodmind.rag.vector_store import VectorStoreManager

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """知识检索器"""

    def __init__(
        self,
        vector_store: VectorStoreManager,
        default_k: int = 5,
        use_rerank: bool = True,
        reranker_model: str = "BAAI/bge-reranker-base",
    ):
        self.vector_store = vector_store
        self.default_k = default_k
        self.use_rerank = use_rerank
        self.reranker_model = reranker_model

    def retrieve(
        self,
        query: str,
        k: Optional[int] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """检索相关文档"""
        k = k or self.default_k

        if self.use_rerank:
            return self.vector_store.search_with_rerank(
                query=query,
                k=k,
                filter=filter,
                reranker_model=self.reranker_model,
            )
        else:
            return self.vector_store.search(query=query, k=k, filter=filter)

    def retrieve_with_scores(
        self,
        query: str,
        k: Optional[int] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[tuple]:
        """带分数的检索"""
        k = k or self.default_k
        return self.vector_store.search_with_scores(query=query, k=k, filter=filter)

    def format_documents(
        self,
        documents: List[Document],
        include_metadata: bool = False,
    ) -> str:
        """将文档列表格式化为文本"""
        if not documents:
            return ""

        formatted_parts = []
        for i, doc in enumerate(documents, 1):
            part = f"[{i}] {doc.page_content}"
            if include_metadata and doc.metadata:
                meta_str = ", ".join(f"{k}: {v}" for k, v in doc.metadata.items() if k not in ["doc_id", "created_at"])
                if meta_str:
                    part += f"\n  (来源: {meta_str})"
            formatted_parts.append(part)

        return "\n\n".join(formatted_parts)

    def retrieve_and_format(
        self,
        query: str,
        k: Optional[int] = None,
        filter: Optional[Dict[str, Any]] = None,
        include_metadata: bool = False,
    ) -> str:
        """检索并格式化结果"""
        documents = self.retrieve(query, k, filter)
        return self.format_documents(documents, include_metadata)