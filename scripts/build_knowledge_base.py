"""
批量构建向量数据库

将 Word、PDF、TXT 等文档批量向量化并存入 ChromaDB。

用法：
  python scripts/build_knowledge_base.py --input ./docs
  python scripts/build_knowledge_base.py --input ./docs --output ./my_vector_db
  python scripts/build_knowledge_base.py --input ./docs --dry-run
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rag.vector_store import VectorStoreManager
from rag.embeddings import EmbeddingManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 支持的文件类型
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


def calculate_file_hash(file_path: Path) -> str:
    """计算文件 MD5，用于增量更新去重"""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_index(index_path: Path) -> dict:
    """加载已处理文件索引"""
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_index(index_path: Path, index: dict):
    """保存已处理文件索引"""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def find_documents(input_dir: Path) -> List[Path]:
    """递归查找所有支持的文档"""
    docs = []
    for ext in SUPPORTED_EXTENSIONS:
        docs.extend(input_dir.rglob(f"*{ext}"))
    return sorted(docs)


def build_knowledge_base(
    input_dir: str,
    output_dir: str = "./vector_db",
    chunk_size: int = 300,
    chunk_overlap: int = 100,
    embedding_model: str = "BAAI/bge-base-zh-v1.5",
    incremental: bool = True,
    dry_run: bool = False,
):
    """
    批量构建向量数据库主流程

    Args:
        input_dir: 文档目录（递归扫描所有 PDF/Word/TXT/MD）
        output_dir: 向量库持久化目录
        chunk_size: 分块大小，默认 300 字符
        chunk_overlap: 分块重叠，默认 100 字符（33% 重叠率）
        embedding_model: Embedding 模型名称
        incremental: 启用增量更新（跳过未修改文件）
        dry_run: 试运行，只统计不入库
    """
    input_path = Path(input_dir).resolve()
    output_path = Path(output_dir).resolve()
    index_path = output_path / ".processed_index.json"

    if not input_path.exists():
        logger.error(f"目录不存在: {input_path}")
        return

    logger.info("=" * 55)
    logger.info("构建向量数据库")
    logger.info("=" * 55)
    logger.info(f"输入目录   : {input_path}")
    logger.info(f"输出目录   : {output_path}")
    logger.info(f"Embedding  : {embedding_model}")
    logger.info(f"分块参数   : size={chunk_size}, overlap={chunk_overlap}")
    logger.info(f"增量更新   : {'是' if incremental else '否（全量重建）'}")
    if dry_run:
        logger.info("【试运行模式】只统计，不入库")

    # ── 1. 扫描文档 ──────────────────────────────────────────
    all_docs = find_documents(input_path)
    if not all_docs:
        logger.warning(f"未找到支持的文档，支持格式: {', '.join(SUPPORTED_EXTENSIONS)}")
        return

    logger.info(f"\n扫描到 {len(all_docs)} 个文档：")
    stats = {}
    for doc in all_docs:
        ext = doc.suffix.lower()
        stats[ext] = stats.get(ext, 0) + 1
    for ext, cnt in sorted(stats.items()):
        logger.info(f"  {ext:8s}: {cnt} 个")

    # ── 2. 增量过滤 ───────────────────────────────────────────
    processed_index = load_index(index_path) if incremental else {}
    to_process = []

    for doc_path in all_docs:
        file_key = str(doc_path.relative_to(input_path))
        file_hash = calculate_file_hash(doc_path)
        if file_key in processed_index and processed_index[file_key]["hash"] == file_hash:
            continue  # 文件未变化，跳过
        to_process.append((doc_path, file_key, file_hash))

    skipped = len(all_docs) - len(to_process)
    logger.info(f"\n待处理: {len(to_process)} 个，跳过（未变化）: {skipped} 个")

    if not to_process:
        logger.info("所有文档均已是最新，无需更新")
        return

    if dry_run:
        logger.info("\n待处理文件列表：")
        for doc_path, _, _ in to_process:
            logger.info(f"  {doc_path.relative_to(input_path)}")
        return

    # ── 3. 初始化向量库 ───────────────────────────────────────
    logger.info("\n初始化向量库...")
    embedding_mgr = EmbeddingManager(model_name=embedding_model)
    store = VectorStoreManager.get_permanent_store(
        base_dir=str(output_path),
        embedding_manager=embedding_mgr,
    )

    # ── 4. 批量入库 ───────────────────────────────────────────
    logger.info("\n开始处理文档...")
    success, failed = 0, []

    for i, (doc_path, file_key, file_hash) in enumerate(to_process, 1):
        logger.info(f"[{i}/{len(to_process)}] {doc_path.name}")
        try:
            store.add_file(
                str(doc_path),
                metadata={
                    "source":        str(doc_path),
                    "filename":      doc_path.name,
                    "file_type":     doc_path.suffix.lower(),
                    "folder":        doc_path.parent.name,
                    "file_hash":     file_hash,
                    "imported_at":   datetime.now().isoformat(),
                },
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            processed_index[file_key] = {
                "hash": file_hash,
                "imported_at": datetime.now().isoformat(),
            }
            success += 1
        except Exception as e:
            logger.error(f"  ✗ 失败: {e}")
            failed.append(doc_path.name)

    # ── 5. 保存索引 & 汇总 ───────────────────────────────────
    save_index(index_path, processed_index)

    logger.info("\n" + "=" * 55)
    logger.info(f"完成！成功: {success}  失败: {len(failed)}")
    logger.info(f"向量库总文档块数: {store.get_document_count()}")
    if failed:
        logger.info(f"失败文件: {', '.join(failed)}")
    logger.info("=" * 55)


def main():
    parser = argparse.ArgumentParser(
        description="将 Word/PDF/TXT 文档批量入库到向量数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python scripts/build_knowledge_base.py --input ./docs

  # 指定输出目录
  python scripts/build_knowledge_base.py --input ./docs --output ./my_vector_db

  # 试运行，查看会处理哪些文件
  python scripts/build_knowledge_base.py --input ./docs --dry-run

  # 使用高精度 Embedding 模型全量重建
  python scripts/build_knowledge_base.py --input ./docs \\
      --embedding-model BAAI/bge-large-zh-v1.5 \\
      --no-incremental
        """,
    )
    parser.add_argument("--input",  "-i", required=True, help="文档目录路径")
    parser.add_argument("--output", "-o", default="./vector_db", help="向量库输出目录（默认: ./vector_db）")
    parser.add_argument("--chunk-size",    type=int, default=300, help="分块大小（默认: 300）")
    parser.add_argument("--chunk-overlap", type=int, default=100, help="分块重叠（默认: 100）")
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-base-zh-v1.5",
        choices=["BAAI/bge-small-zh-v1.5", "BAAI/bge-base-zh-v1.5", "BAAI/bge-large-zh-v1.5"],
        help="Embedding 模型（默认: bge-base，更高精度用 bge-large）",
    )
    parser.add_argument("--no-incremental", action="store_true", help="强制全量重建")
    parser.add_argument("--dry-run",        action="store_true", help="试运行，只统计不入库")

    args = parser.parse_args()
    build_knowledge_base(
        input_dir=args.input,
        output_dir=args.output,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embedding_model=args.embedding_model,
        incremental=not args.no_incremental,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
