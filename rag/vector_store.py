"""
向量数据库管理

使用 ChromaDB 作为向量存储，支持持久化和会话级隔离。
"""

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma

from rag.embeddings import EmbeddingManager

logger = logging.getLogger(__name__)

SMALL_DOC_THRESHOLD = 10000
SMALL_DOC_TOKEN_THRESHOLD = 3000


class VectorStoreManager:
    """向量数据库管理器"""
    
    _instances: Dict[str, 'VectorStoreManager'] = {}
    # Cross-Encoder 重排序器类级缓存，所有实例共享，避免重复加载
    _reranker_cache: Dict[str, Any] = {}
    
    def __new__(
        cls,
        persist_dir: str,
        collection_name: str = "default",
        embedding_manager: Optional[EmbeddingManager] = None,
    ):
        cache_key = f"{persist_dir}:{collection_name}"
        if cache_key not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[cache_key] = instance
        return cls._instances[cache_key]
    
    def __init__(
        self,
        persist_dir: str,
        collection_name: str = "default",
        embedding_manager: Optional[EmbeddingManager] = None,
    ):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self.embedding_manager = embedding_manager or EmbeddingManager()
        
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        
        self._vectorstore: Optional[Chroma] = None
        self._initialized = True
        
        logger.info(f"向量存储管理器初始化: {persist_dir}, 集合: {collection_name}")
    
    @property
    def vectorstore(self) -> Chroma:
        """获取向量存储实例（懒加载）"""
        if self._vectorstore is None:
            self._vectorstore = Chroma(
                persist_directory=str(self.persist_dir),
                embedding_function=self.embedding_manager.get_embeddings(),
                collection_name=self.collection_name,
            )
        return self._vectorstore
    
    def add_documents(
        self,
        documents: List[Document],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """
        添加文档到向量库
        
        Args:
            documents: 文档列表
            metadatas: 元数据列表
            
        Returns:
            添加的文档ID列表
        """
        if not documents:
            return []
        
        if metadatas:
            for i, meta in enumerate(metadatas):
                if i < len(documents):
                    documents[i].metadata.update(meta)
        
        for doc in documents:
            if "doc_id" not in doc.metadata:
                doc.metadata["doc_id"] = str(uuid.uuid4())
            if "created_at" not in doc.metadata:
                doc.metadata["created_at"] = datetime.now().isoformat()
        
        ids = self.vectorstore.add_documents(documents)
        
        logger.info(f"添加 {len(documents)} 个文档到向量库，集合: {self.collection_name}")
        return ids
    
    def add_text(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 300,
        chunk_overlap: int = 100,
    ) -> List[str]:
        """
        添加文本到向量库（自动分块）

        分块策略说明：
        ─────────────────────────────────────────────────────────
        1. 表格感知（优先级最高）：
           调用 _split_preserving_tables()，将连续 | 开头的行识别为
           Markdown 表格块，整体作为一个原子块保留，不做任何拆分，
           确保表头+分隔行+数据行始终在同一块内。

        2. 目标块大小（chunk_size=300）：
           非表格段落每块约 300 字符，语义聚焦，减少检索噪声。

        3. 重叠窗口（chunk_overlap=100）：
           相邻非表格块保留 100 字符重叠（约 33%），
           避免长句/指代在块边界被截断时上下文丢失。

        4. 分隔符优先级（从高到低递归尝试，仅作用于非表格段落）：
           ① "\n\n" —— 段落边界
           ② "\n"   —— 行边界
           ③ "。！？"—— 中文句末标点
           ④ "；"   —— 分句标点
           ⑤ "，"   —— 逗号
           ⑥ " "    —— 英文词边界
           ⑦ ""     —— 兜底按字符截断（尽量避免触发）

        5. Markdown 结构文档（如 SKILL.md）建议通过 add_file() 传入，
           内部会先按标题层级切分，再对超长节做表格感知递归分块。
        ─────────────────────────────────────────────────────────

        Args:
            text: 文本内容
            metadata: 元数据
            chunk_size: 每块最大字符数，默认 300
            chunk_overlap: 相邻非表格块重叠字符数，默认 100

        Returns:
            添加的文档ID列表
        """
        if not text.strip():
            return []

        metadata = metadata or {}

        raw_chunks = self._split_preserving_tables(text, chunk_size, chunk_overlap)

        documents = [
            Document(page_content=chunk, metadata={**metadata, "chunk_index": i})
            for i, chunk in enumerate(raw_chunks)
        ]

        return self.add_documents(documents)

    def _split_preserving_tables(
        self,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> List[str]:
        """
        表格感知分块核心方法。

        处理逻辑：
        1. 逐行扫描，将文本划分为"表格段"和"非表格段"两种区间：
           - 表格段：连续以 | 开头（含前导空格）的行，整体作为一个原子块，
                     即使超过 chunk_size 也不拆分（强制保留完整表格）。
           - 表格标题：紧跟表格的标题行（Markdown标题或短文本标题），
                     会被合并到表格块中，确保"标题+表格"不分离。
           - 非表格段：普通段落，拼回字符串后交给
                       RecursiveCharacterTextSplitter 做递归分块。
        2. 按原文顺序合并两类块，保证最终顺序与源文档一致。

        Args:
            text: 待分块的原始文本
            chunk_size: 非表格段的最大块大小
            chunk_overlap: 非表格段相邻块的重叠字符数

        Returns:
            分块字符串列表
        """
        import re
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )

        lines = text.split("\n")
        result_chunks: List[str] = []

        non_table_buf: List[str] = []
        table_buf: List[str] = []

        def is_table_title(line: str) -> bool:
            """判断一行是否可能是表格标题"""
            stripped = line.strip()
            if not stripped:
                return False
            
            if re.match(r"^#{1,6}\s+", stripped):
                return True
            
            title_keywords = ["表", "Table", "TABLE", "表格", "列表", "清单"]
            if any(kw in stripped for kw in title_keywords):
                if len(stripped) < 100:
                    return True
            
            if len(stripped) < 50 and not stripped.endswith(("。", "！", "？", ".", "!", "?")):
                return True
            
            return False

        def extract_table_titles() -> List[str]:
            """从非表格缓冲区末尾提取表格标题行"""
            titles = []
            i = len(non_table_buf) - 1
            
            while i >= 0:
                line = non_table_buf[i]
                if not line.strip():
                    titles.insert(0, line)
                    i -= 1
                elif is_table_title(line):
                    titles.insert(0, line)
                    i -= 1
                else:
                    break
            
            return titles

        def flush_non_table() -> None:
            """将非表格缓冲区内容做递归分块后追加到结果"""
            if non_table_buf:
                segment = "\n".join(non_table_buf).strip()
                if segment:
                    result_chunks.extend(recursive_splitter.split_text(segment))
                non_table_buf.clear()

        def flush_table() -> None:
            """将表格缓冲区内容整体作为一个原子块追加到结果"""
            if table_buf:
                table_text = "\n".join(table_buf).strip()
                if table_text:
                    result_chunks.append(table_text)
                table_buf.clear()

        for line in lines:
            if re.match(r"^\s*\|", line):
                if not table_buf:
                    titles = extract_table_titles()
                    if titles:
                        for title in titles:
                            non_table_buf.remove(title)
                        table_buf.extend(titles)
                    flush_non_table()
                table_buf.append(line)
            else:
                if table_buf:
                    flush_table()
                non_table_buf.append(line)

        flush_non_table()
        flush_table()

        return result_chunks

    def add_file(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 300,
        chunk_overlap: int = 100,
    ) -> List[str]:
        """
        添加文件到向量库

        分块策略因文件类型而异：
        - .md 文件：两阶段分块
            阶段一：MarkdownHeaderTextSplitter 按标题层级（#/##/###）切分，
                    保留每节完整的标题上下文，避免跨节内容混入同一块。
            阶段二：对超过 chunk_size 的节再用 RecursiveCharacterTextSplitter
                    做细粒度递归分块（含重叠窗口）。
        - 其他文件：直接调用 add_text() 走递归分块策略。

        Args:
            file_path: 文件路径
            metadata: 元数据
            chunk_size: 每块最大字符数，默认 300
            chunk_overlap: 相邻块重叠字符数，默认 100

        Returns:
            添加的文档ID列表
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        metadata = metadata or {}
        metadata["source"] = str(path)
        metadata["filename"] = path.name
        metadata["file_type"] = path.suffix.lower()

        # Markdown 文件：优先按标题层级切分，保留结构上下文
        if path.suffix.lower() == ".md":
            return self._add_markdown_file(path, metadata, chunk_size, chunk_overlap)

        text = self._extract_text(path)
        return self.add_text(text, metadata, chunk_size, chunk_overlap)

    def _add_markdown_file(
        self,
        path: Path,
        metadata: Dict[str, Any],
        chunk_size: int,
        chunk_overlap: int,
    ) -> List[str]:
        """
        Markdown 文件两阶段 + 表格感知分块：
          阶段一：MarkdownHeaderTextSplitter 按标题边界切分，
                  每块自动携带 h1/h2/h3 标题层级元数据。
          阶段二：对超过 chunk_size 的节调用 _split_preserving_tables()，
                  表格段整体保留，非表格段递归细分（含重叠窗口）；
                  子块均继承父节标题元数据。
        """
        from langchain_text_splitters import MarkdownHeaderTextSplitter

        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []

        # 阶段一：按标题层级切分，strip_headers=False 保留标题行便于独立理解
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
            ],
            strip_headers=False,
        )
        header_docs = header_splitter.split_text(text)

        final_docs = []
        chunk_index = 0
        for doc in header_docs:
            if len(doc.page_content) <= chunk_size:
                # 节内容未超限，直接保留
                doc.metadata.update({**metadata, "chunk_index": chunk_index})
                final_docs.append(doc)
                chunk_index += 1
            else:
                # 节内容过长：表格感知递归细分，子块继承父节标题元数据
                sub_chunks = self._split_preserving_tables(
                    doc.page_content, chunk_size, chunk_overlap
                )
                for sub in sub_chunks:
                    final_docs.append(
                        Document(
                            page_content=sub,
                            metadata={**metadata, **doc.metadata, "chunk_index": chunk_index},
                        )
                    )
                    chunk_index += 1

        logger.info(
            f"Markdown 文件 {path.name} 分块完成："
            f"标题节数={len(header_docs)}，最终块数={len(final_docs)}"
        )
        return self.add_documents(final_docs)

    def _extract_text(self, path: Path) -> str:
        """从文件中提取文本"""
        suffix = path.suffix.lower()
        
        if suffix in [".txt", ".md", ".py", ".json", ".csv"]:
            return path.read_text(encoding="utf-8")
        
        if suffix == ".pdf":
            return self._extract_pdf(path)
        
        if suffix in [".docx", ".doc"]:
            return self._extract_docx(path)
        
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"无法读取文件 {path}: {e}")
            return ""
    
    def _extract_pdf(self, path: Path) -> str:
        """提取 PDF 文本"""
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except ImportError:
            logger.warning("未安装 pypdf，无法读取 PDF")
            return ""
        except Exception as e:
            logger.error(f"读取 PDF 失败: {e}")
            return ""
    
    def _extract_docx(self, path: Path) -> str:
        """提取 Word 文档文本（包含表格），支持 .docx 和 .doc"""
        suffix = path.suffix.lower()
        
        if suffix == ".doc":
            return self._extract_doc(path)
        
        try:
            from docx import Document as DocxDocument
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            from docx.oxml.ns import qn
            
            doc = DocxDocument(str(path))
            parts = []
            
            def iter_block_items(parent):
                """
                遍历文档中的所有块级元素（段落和表格）
                保持原始顺序
                """
                if hasattr(parent, 'element'):
                    parent_elm = parent.element.body
                else:
                    parent_elm = parent
                
                for child in parent_elm.iterchildren():
                    if child.tag == qn('w:p'):
                        yield Paragraph(child, parent)
                    elif child.tag == qn('w:tbl'):
                        yield Table(child, parent)
            
            def table_to_markdown(table: Table) -> str:
                """将 Word 表格转换为 Markdown 格式"""
                rows = []
                for row in table.rows:
                    cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    rows.append(cells)
                
                if not rows:
                    return ""
                
                col_count = len(rows[0])
                md_lines = []
                
                header = "| " + " | ".join(rows[0]) + " |"
                md_lines.append(header)
                
                
                separator = "| " + " | ".join(["---"] * col_count) + " |"
                md_lines.append(separator)
                
                for row in rows[1:]:
                    while len(row) < col_count:
                        row.append("")
                    md_lines.append("| " + " | ".join(row[:col_count]) + " |")
                
                return "\n".join(md_lines)
            
            for block in iter_block_items(doc):
                if isinstance(block, Paragraph):
                    text = block.text.strip()
                    if text:
                        parts.append(text)
                elif isinstance(block, Table):
                    table_md = table_to_markdown(block)
                    if table_md:
                        parts.append(table_md)
            
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"读取 Word 文档失败: {e}")
            return ""
    
    def _extract_doc(self, path: Path) -> str:
        """提取旧版 .doc 文档文本"""
        try:
            import platform
            if platform.system() != 'Windows':
                logger.warning(".doc 格式仅支持 Windows 系统，尝试使用其他方法")
                return self._extract_doc_via_textract(path)
            
            try:
                import win32com.client
                import pythoncom
                
                pythoncom.CoInitialize()
                
                word = win32com.client.Dispatch("Word.Application")
                word.Visible = False
                
                doc = word.Documents.Open(str(path.absolute()))
                text = doc.Content.Text
                
                doc.Close(False)
                word.Quit()
                
                pythoncom.CoUninitialize()
                
                return text
                
            except ImportError:
                logger.warning("未安装 pywin32，尝试使用 textract")
                return self._extract_doc_via_textract(path)
            except Exception as e:
                logger.error(f"使用 Word COM 读取 .doc 失败: {e}")
                return self._extract_doc_via_textract(path)
                
        except Exception as e:
            logger.error(f"读取 .doc 文档失败: {e}")
            return ""
    
    def _extract_doc_via_textract(self, path: Path) -> str:
        """使用 textract 提取 .doc 文档（备选方案）"""
        try:
            import textract
            text = textract.process(str(path))
            return text.decode('utf-8') if isinstance(text, bytes) else text
        except ImportError:
            logger.warning("未安装 textract，无法读取 .doc 文件。请安装: pip install textract")
            return ""
        except Exception as e:
            logger.error(f"使用 textract 读取 .doc 失败: {e}")
            return ""
    
    def search(
        self,
        query: str,
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """
        相似度检索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            filter: 元数据过滤条件
            
        Returns:
            相关文档列表
        """
        if filter:
            results = self.vectorstore.similarity_search(
                query, k=k, filter=filter
            )
        else:
            results = self.vectorstore.similarity_search(query, k=k)
        
        logger.info(f"检索查询: {query[:50]}..., 返回 {len(results)} 个结果")
        return results
    
    def search_with_scores(
        self,
        query: str,
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[tuple[Document, float]]:
        """
        带分数的相似度检索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            filter: 元数据过滤条件
            
        Returns:
            (文档, 分数) 列表
        """
        if filter:
            results = self.vectorstore.similarity_search_with_score(
                query, k=k, filter=filter
            )
        else:
            results = self.vectorstore.similarity_search_with_score(query, k=k)
        
        return results

    def search_with_rerank(
        self,
        query: str,
        k: int = 5,
        fetch_k: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        reranker_model: str = "BAAI/bge-reranker-base",
    ) -> List[Document]:
        """
        两阶段检索：向量召回 → Cross-Encoder 重排序

        两阶段流程：
        ─────────────────────────────────────────────────────
        阶段一（粗召回）：
          向量相似度检索召回 fetch_k 条候选文档（默认 50 条）。
          向量相似度基于 Embedding 内积/余弦距离，速度快但精度有限，
          适合从大规模语料中快速筛出相关候选集。

        阶段二（精排/重排序）：
          使用 Cross-Encoder（BAAI/bge-reranker-base）对每个候选文档
          与 query 做逐对打分。Cross-Encoder 将 (query, doc) 拼接后
          一起输入模型，比 Bi-Encoder 的独立编码精度更高，
          能捕捉 query 与文档之间的细粒度语义交互。
          按分数降序排列后取 top-k 返回。

        模型选型说明（BAAI/bge-reranker-base）：
          - 支持中英文混合，适合本项目中文洪水报告场景
          - 120M 参数，CPU 可运行，首次调用自动从 HuggingFace 加载
          - 类级缓存（_reranker_cache），同进程内只加载一次
        ─────────────────────────────────────────────────────

        Args:
            query: 查询文本
            k: 最终返回的文档数量（精排后 top-k），默认 5
            fetch_k: 第一阶段向量召回的候选数量，默认 50
            filter: 元数据过滤条件（透传给向量检索）
            reranker_model: Cross-Encoder 模型名称，
                            默认 "BAAI/bge-reranker-base"

        Returns:
            重排序后的 top-k 文档列表（按相关性降序）
        """
        # ── 阶段一：向量粗召回 ──────────────────────────────
        fetch_k = max(fetch_k, k)  # 保证候选数不少于目标返回数
        if filter:
            candidates = self.vectorstore.similarity_search(
                query, k=fetch_k, filter=filter
            )
        else:
            candidates = self.vectorstore.similarity_search(query, k=fetch_k)

        if not candidates:
            return []

        logger.info(
            f"两阶段检索 — 阶段一召回: {len(candidates)} 条候选，"
            f"查询: {query[:50]}..."
        )

        # ── 阶段二：Cross-Encoder 重排序 ────────────────────
        reranker = self._get_reranker(reranker_model)
        # 构造 (query, doc_content) 对列表
        pairs = [(query, doc.page_content) for doc in candidates]
        scores = reranker.predict(pairs)  # 返回每对的相关性分数

        # 按分数降序排列，取 top-k
        ranked = sorted(
            zip(scores, candidates),
            key=lambda x: x[0],
            reverse=True,
        )
        top_docs = [doc for _, doc in ranked[:k]]

        logger.info(
            f"两阶段检索 — 阶段二精排完成，返回 top-{len(top_docs)} 条，"
            f"最高分={ranked[0][0]:.4f}，最低分={ranked[k-1][0]:.4f}"
        )
        return top_docs

    def _get_reranker(self, model_name: str) -> Any:
        """
        获取 Cross-Encoder 重排序器（懒加载 + 类级缓存）。

        首次调用时从 HuggingFace 加载模型并缓存至 _reranker_cache，
        后续调用直接返回缓存实例，同进程内不重复初始化。

        依赖：sentence-transformers（需在 requirements.txt 中声明）

        Args:
            model_name: HuggingFace 模型标识

        Returns:
            sentence_transformers.CrossEncoder 实例
        """
        if model_name not in VectorStoreManager._reranker_cache:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise ImportError(
                    "重排序功能需要 sentence-transformers，"
                    "请执行: pip install sentence-transformers"
                ) from exc

            logger.info(f"加载 Cross-Encoder 重排序模型: {model_name}")
            VectorStoreManager._reranker_cache[model_name] = CrossEncoder(
                model_name,
                max_length=512,  # 限制单次输入长度，防止 OOM
            )
            logger.info(f"Cross-Encoder 模型加载完成: {model_name}")

        return VectorStoreManager._reranker_cache[model_name]

    def delete_by_metadata(self, filter: Dict[str, Any]) -> int:
        """
        按元数据删除文档
        
        Args:
            filter: 元数据过滤条件
            
        Returns:
            删除的文档数量
        """
        try:
            collection = self.vectorstore._collection
            
            if filter:
                where_clause = self._build_where_clause(filter)
                result = collection.delete(where=where_clause)
                deleted_count = len(result.get("ids", []))
            else:
                result = collection.delete()
                deleted_count = len(result.get("ids", []))
            
            logger.info(f"删除 {deleted_count} 个文档，过滤条件: {filter}")
            return deleted_count
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return 0
    
    def _build_where_clause(self, filter: Dict[str, Any]) -> Dict[str, Any]:
        """构建 ChromaDB where 子句"""
        if len(filter) == 1:
            key, value = next(iter(filter.items()))
            return {key: value}
        elif len(filter) > 1:
            return {"$and": [{k: v} for k, v in filter.items()]}
        return {}
    
    def clear(self):
        """清空当前集合"""
        self.delete_by_metadata({})
        logger.info(f"集合 {self.collection_name} 已清空")
    
    def delete_collection(self):
        """删除整个集合"""
        try:
            self.vectorstore.delete_collection()
            self._vectorstore = None
            logger.info(f"集合 {self.collection_name} 已删除")
        except Exception as e:
            logger.error(f"删除集合失败: {e}")
    
    def get_document_count(self) -> int:
        """获取文档数量"""
        try:
            return self.vectorstore._collection.count()
        except Exception:
            return 0
    
    @classmethod
    def get_session_store(
        cls,
        base_dir: str,
        session_id: str,
        embedding_manager: Optional[EmbeddingManager] = None,
    ) -> 'VectorStoreManager':
        """
        获取会话级向量存储
        
        Args:
            base_dir: 基础目录
            session_id: 会话ID
            embedding_manager: Embedding 管理器
            
        Returns:
            会话级向量存储管理器
        """
        persist_dir = os.path.join(base_dir, "sessions", session_id)
        return cls(
            persist_dir=persist_dir,
            collection_name=f"session_{session_id}",
            embedding_manager=embedding_manager,
        )
    
    @classmethod
    def get_permanent_store(
        cls,
        base_dir: str,
        embedding_manager: Optional[EmbeddingManager] = None,
    ) -> 'VectorStoreManager':
        """
        获取永久向量存储
        
        Args:
            base_dir: 基础目录
            embedding_manager: Embedding 管理器
            
        Returns:
            永久向量存储管理器
        """
        persist_dir = os.path.join(base_dir, "permanent")
        return cls(
            persist_dir=persist_dir,
            collection_name="permanent",
            embedding_manager=embedding_manager,
        )
    
    @classmethod
    def cleanup_session(cls, base_dir: str, session_id: str):
        """
        清理会话级向量存储
        
        Args:
            base_dir: 基础目录
            session_id: 会话ID
        """
        session_dir = Path(base_dir) / "sessions" / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)
            logger.info(f"已清理会话向量存储: {session_id}")
    
    @classmethod
    def reset_all(cls):
        """重置所有实例（用于测试）"""
        cls._instances.clear()
