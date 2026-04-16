"""
批量构建向量数据库。

知识组织策略：
1. Word / PDF / TXT / MD 按正文切片入库；扫描 PDF 在启用 OCR 时自动走 OCR 降级链路。
2. Excel / GIS / 图片按文件级摘要入库，不直接写入具体内容。
3. 所有条目都补充目录层级键，便于后续按项目目录过滤检索。

用法：
  python scripts/build_knowledge_base.py --input ./docs
  python scripts/build_knowledge_base.py --input ./docs --output ./my_vector_db
  python scripts/build_knowledge_base.py --input ./docs --dry-run
"""

import argparse
from collections import defaultdict
import hashlib
import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from PIL import Image

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rag.vector_store import VectorStoreManager
from rag.embeddings import EmbeddingManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
GIS_EXTENSIONS = {".shp", ".geojson", ".gdb", ".gpkg", ".tif", ".tiff", ".dwg", ".kml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | EXCEL_EXTENSIONS | GIS_EXTENSIONS | IMAGE_EXTENSIONS


def detect_asset_kind(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text_document"
    if ext in EXCEL_EXTENSIONS:
        return "excel_asset"
    if ext in GIS_EXTENSIONS:
        return "gis_asset"
    if ext in IMAGE_EXTENSIONS:
        return "image_asset"
    return "file_asset"


def build_folder_metadata(file_path: Path, root_path: Path) -> Dict[str, str]:
    relative = Path(root_path.name) / file_path.relative_to(root_path)
    folders = list(relative.parts[:-1])
    metadata: Dict[str, str] = {
        "relative_path": str(relative).replace("\\", "/"),
        "display_path": f"./{str(relative).replace('\\', '/')}",
        "folder_path": "/".join(folders),
        "folder_keys": " > ".join(folders),
    }
    for index, folder in enumerate(folders[:6], 1):
        metadata[f"folder_level_{index}"] = folder
    return metadata


def build_common_metadata(file_path: Path, root_path: Path, file_hash: str) -> Dict[str, str]:
    metadata = {
        "source": str(file_path),
        "filename": file_path.name,
        "file_type": file_path.suffix.lower(),
        "asset_kind": detect_asset_kind(file_path),
        "file_hash": file_hash,
        "imported_at": datetime.now().isoformat(),
    }
    metadata.update(build_folder_metadata(file_path, root_path))
    return metadata


def trim_text(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def load_ocr_engine(enable_ocr: bool) -> Optional[Any]:
    if not enable_ocr:
        return None

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        logger.warning("未安装 rapidocr_onnxruntime，图片 OCR 已自动降级为仅文件级索引。")
        return None

    try:
        return RapidOCR()
    except Exception as exc:
        logger.warning(f"初始化 OCR 引擎失败，图片 OCR 已自动降级: {exc}")
        return None


def extract_image_ocr_text(file_path: Path, ocr_engine: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    if ocr_engine is None:
        return "", {
            "ocr_status": "disabled",
            "ocr_line_count": "0",
        }

    try:
        result, _ = ocr_engine(str(file_path))
    except Exception as exc:
        return "", {
            "ocr_status": f"failed: {trim_text(str(exc), 120)}",
            "ocr_line_count": "0",
        }

    if not result:
        return "", {
            "ocr_status": "empty",
            "ocr_line_count": "0",
        }

    lines: List[str] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        line_text = str(item[1] or "").strip()
        if line_text:
            lines.append(line_text)

    merged = "\n".join(lines).strip()
    return merged, {
        "ocr_status": "success" if merged else "empty",
        "ocr_line_count": str(len(lines)),
    }


def extract_pdf_text_fast(file_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("未安装 pypdf，无法执行 PDF 文本预检测。")
        return ""

    try:
        reader = PdfReader(str(file_path))
        texts: List[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                texts.append(page_text)
        return "\n".join(texts).strip()
    except Exception as exc:
        logger.warning(f"PDF 文本预检测失败，将尝试 OCR 降级: {file_path.name}, {exc}")
        return ""


def extract_pdf_ocr_text(file_path: Path, ocr_engine: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    if ocr_engine is None:
        return "", {
            "pdf_ocr_status": "disabled",
            "pdf_ocr_page_count": "0",
        }

    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning("未安装 pdf2image，扫描 PDF OCR 已自动降级。")
        return "", {
            "pdf_ocr_status": "pdf2image_missing",
            "pdf_ocr_page_count": "0",
        }

    try:
        images = convert_from_path(str(file_path), dpi=200)
    except Exception as exc:
        logger.warning(f"扫描 PDF 转图片失败，OCR 已自动降级: {file_path.name}, {exc}")
        return "", {
            "pdf_ocr_status": f"render_failed: {trim_text(str(exc), 120)}",
            "pdf_ocr_page_count": "0",
        }

    page_texts: List[str] = []
    for image in images:
        try:
            result, _ = ocr_engine(image)
        except Exception:
            result = None
        lines: List[str] = []
        if result:
            for item in result:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                line_text = str(item[1] or "").strip()
                if line_text:
                    lines.append(line_text)
        if lines:
            page_texts.append("\n".join(lines))

    merged = "\n\n".join(page_texts).strip()
    return merged, {
        "pdf_ocr_status": "success" if merged else "empty",
        "pdf_ocr_page_count": str(len(images)),
    }


def extract_docx_text_with_image_ocr(file_path: Path, ocr_engine: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    try:
        from docx import Document as DocxDocument
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ImportError("需要安装 python-docx 才能解析 Word 文档") from exc

    doc = DocxDocument(str(file_path))
    parts: List[str] = []

    def iter_block_items(parent):
        if hasattr(parent, "element"):
            parent_elm = parent.element.body
        else:
            parent_elm = parent

        for child in parent_elm.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                yield Table(child, parent)

    def table_to_markdown(table: Table) -> str:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            rows.append(cells)

        if not rows:
            return ""

        col_count = len(rows[0])
        md_lines = []
        md_lines.append("| " + " | ".join(rows[0]) + " |")
        md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
        for row in rows[1:]:
            while len(row) < col_count:
                row.append("")
            md_lines.append("| " + " | ".join(row[:col_count]) + " |")
        return "\n".join(md_lines)

    def get_paragraph_image_rel_ids(paragraph: Paragraph) -> List[str]:
        rel_ids: List[str] = []
        try:
            blips = paragraph._p.xpath('.//*[local-name()="blip"]')
        except Exception:
            blips = []
        for blip in blips:
            rel_id = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            if rel_id and rel_id not in rel_ids:
                rel_ids.append(rel_id)
        return rel_ids

    paragraph_records: List[Dict[str, Any]] = []

    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            paragraph_records.append({
                "text": text,
                "rel_ids": get_paragraph_image_rel_ids(block),
            })
            if text:
                parts.append(text)
        elif isinstance(block, Table):
            table_md = table_to_markdown(block)
            if table_md:
                parts.append(table_md)

    image_contexts: Dict[str, List[Dict[str, str]]] = {}
    for index, record in enumerate(paragraph_records):
        rel_ids = record.get("rel_ids", []) or []
        if not rel_ids:
            continue

        prev_text = ""
        for prev_index in range(index - 1, -1, -1):
            candidate = str(paragraph_records[prev_index].get("text", "") or "").strip()
            if candidate:
                prev_text = candidate
                break

        next_text = ""
        for next_index in range(index + 1, len(paragraph_records)):
            candidate = str(paragraph_records[next_index].get("text", "") or "").strip()
            if candidate:
                next_text = candidate
                break

        context_entry = {
            "anchor_text": str(record.get("text", "") or "").strip(),
            "prev_text": prev_text,
            "next_text": next_text,
        }
        for rel_id in rel_ids:
            image_contexts.setdefault(rel_id, []).append(context_entry)

    rel_id_to_image_name: Dict[str, str] = {}
    try:
        for rel_id, rel in doc.part.rels.items():
            target_ref = str(getattr(rel, "target_ref", "") or "")
            if "media/" in target_ref:
                rel_id_to_image_name[rel_id] = Path(target_ref).name
    except Exception:
        pass

    image_name_to_contexts: Dict[str, List[Dict[str, str]]] = {}
    for rel_id, entries in image_contexts.items():
        image_name = rel_id_to_image_name.get(rel_id)
        if not image_name:
            continue
        image_name_to_contexts.setdefault(image_name, []).extend(entries)

    image_blocks: List[str] = []
    image_names: List[str] = []
    image_count = 0
    ocr_success_count = 0

    try:
        with zipfile.ZipFile(file_path, "r") as archive:
            media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
            for media_name in media_files:
                image_count += 1
                image_basename = Path(media_name).name
                image_names.append(image_basename)
                if ocr_engine is None:
                    continue
                try:
                    image_bytes = archive.read(media_name)
                    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    result, _ = ocr_engine(image)
                except Exception:
                    result = None

                lines: List[str] = []
                if result:
                    for item in result:
                        if not isinstance(item, (list, tuple)) or len(item) < 2:
                            continue
                        line_text = str(item[1] or "").strip()
                        if line_text:
                            lines.append(line_text)

                if lines:
                    ocr_success_count += 1
                    related_contexts = image_name_to_contexts.get(image_basename, [])
                    context_lines: List[str] = []
                    for context_index, context in enumerate(related_contexts[:2], 1):
                        anchor_text = trim_text(context.get("anchor_text", ""), 120)
                        prev_text = trim_text(context.get("prev_text", ""), 120)
                        next_text = trim_text(context.get("next_text", ""), 120)
                        if anchor_text:
                            context_lines.append(f"锚点段落{context_index}: {anchor_text}")
                        if prev_text:
                            context_lines.append(f"前文{context_index}: {prev_text}")
                        if next_text:
                            context_lines.append(f"后文{context_index}: {next_text}")
                    image_blocks.append("\n".join([
                        f"[文档图片 {image_count}]",
                        f"图片文件名: {image_basename}",
                        f"OCR文本: {' '.join(lines)}",
                        *context_lines,
                    ]))
    except Exception as exc:
        image_blocks.append(f"[文档图片提取失败]\n错误摘要: {trim_text(str(exc), 120)}")

    if image_blocks:
        parts.append("[文档内图片OCR]\n" + "\n\n".join(image_blocks))

    metadata = {
        "docx_image_count": str(image_count),
        "docx_image_ocr_success_count": str(ocr_success_count),
        "docx_image_ocr_status": "success" if ocr_success_count > 0 else ("disabled" if ocr_engine is None else "empty"),
        "docx_image_names": ", ".join(image_names[:20]),
        "docx_image_ocr_preview": trim_text(" | ".join(image_blocks), 300) if image_blocks else "",
    }
    return "\n\n".join(part for part in parts if part).strip(), metadata


def add_text_document_with_fallback(
    store: VectorStoreManager,
    file_path: Path,
    source_metadata: Dict[str, str],
    chunk_size: int,
    chunk_overlap: int,
    enable_ocr: bool,
    ocr_engine: Optional[Any],
) -> Dict[str, str]:
    metadata = {
        **source_metadata,
        "index_mode": "content_chunk",
        "content_granularity": "text_chunk",
    }

    suffix = file_path.suffix.lower()

    if suffix == ".docx":
        extracted_text, docx_meta = extract_docx_text_with_image_ocr(file_path, ocr_engine if enable_ocr else None)
        if extracted_text:
            effective_metadata = {
                **metadata,
                **docx_meta,
                "docx_ingest_mode": "text_with_image_ocr" if enable_ocr else "text_only",
            }
            store.add_text(
                extracted_text,
                metadata=effective_metadata,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            return effective_metadata

    if suffix != ".pdf":
        store.add_file(
            str(file_path),
            metadata=metadata,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return metadata

    extracted_text = extract_pdf_text_fast(file_path)
    if extracted_text:
        effective_metadata = {
            **metadata,
            "pdf_ingest_mode": "native_text",
        }
        store.add_text(
            extracted_text,
            metadata=effective_metadata,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return effective_metadata

    ocr_text, ocr_meta = extract_pdf_ocr_text(file_path, ocr_engine if enable_ocr else None)
    if ocr_text:
        effective_metadata = {
            **metadata,
            **ocr_meta,
            "pdf_ingest_mode": "ocr_fallback",
        }
        store.add_text(
            ocr_text,
            metadata=effective_metadata,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return effective_metadata

    placeholder = "\n".join([
        "文件类型: PDF 文档",
        f"文件名: {file_path.name}",
        "状态: 当前 PDF 未提取到可用文本。",
        "说明: 该文件可能是扫描件；若需内容检索，请确认 OCR 依赖和 PDF 渲染环境可用。",
    ])
    effective_metadata = {
        **metadata,
        **ocr_meta,
        "index_mode": "file_summary",
        "content_granularity": "pdf_file_summary",
        "pdf_ingest_mode": "empty_placeholder",
    }
    store.add_text(
        placeholder,
        metadata=effective_metadata,
        chunk_size=max(chunk_size, 500),
        chunk_overlap=0,
    )
    return effective_metadata


def summarize_excel_file(file_path: Path, root_path: Path) -> Tuple[str, Dict[str, str]]:
    ext = file_path.suffix.lower()
    summary_lines = [
        f"文件类型: Excel/表格文件",
        f"文件名: {file_path.name}",
    ]
    metadata: Dict[str, str] = {
        "index_mode": "file_summary",
        "content_granularity": "table_summary",
    }

    try:
        if ext in {".csv", ".tsv"}:
            separator = "\t" if ext == ".tsv" else ","
            frame = pd.read_csv(file_path, sep=separator, nrows=5)
            columns = [str(col) for col in frame.columns.tolist()[:20]]
            metadata["sheet_names"] = "__default__"
            metadata["table_fields"] = ", ".join(columns)
            summary_lines.extend([
                "工作表: __default__",
                f"字段摘要: {', '.join(columns) if columns else '无'}",
            ])
        else:
            excel_file = pd.ExcelFile(file_path)
            sheet_summaries: List[str] = []
            all_columns: List[str] = []
            metadata["sheet_names"] = ", ".join(excel_file.sheet_names[:20])
            for sheet_name in excel_file.sheet_names[:8]:
                try:
                    frame = excel_file.parse(sheet_name, nrows=5)
                    columns = [str(col) for col in frame.columns.tolist()[:12]]
                except Exception:
                    columns = []
                for column in columns:
                    if column not in all_columns:
                        all_columns.append(column)
                field_text = ", ".join(columns) if columns else "无可识别字段"
                sheet_summaries.append(f"{sheet_name}: {field_text}")
            metadata["table_fields"] = ", ".join(all_columns[:30])
            summary_lines.append(f"工作表数量: {len(excel_file.sheet_names)}")
            summary_lines.append(f"工作表摘要: {'; '.join(sheet_summaries) if sheet_summaries else '无'}")
    except Exception as exc:
        summary_lines.append(f"表格摘要提取失败: {exc}")

    summary_lines.append("使用方式: 该文件当前只建立表级索引；召回后再根据任务目标读取并分析具体表格数据。")
    metadata.update(build_folder_metadata(file_path, root_path))
    return "\n".join(summary_lines), metadata


def summarize_gis_file(file_path: Path, root_path: Path) -> Tuple[str, Dict[str, str]]:
    summary = "\n".join([
        "文件类型: GIS/地理信息数据文件",
        f"文件名: {file_path.name}",
        f"扩展名: {file_path.suffix.lower()}",
        f"资产形态: {'目录型地理数据库' if file_path.is_dir() else '文件型 GIS 数据'}",
        "使用方式: 该文件只建立文件级索引，不解析几何内容；召回后直接返回文件路径供 GIS 专项流程处理。",
    ])
    metadata = {
        "index_mode": "file_summary",
        "content_granularity": "gis_file_summary",
    }
    metadata.update(build_folder_metadata(file_path, root_path))
    return summary, metadata


def summarize_image_file(file_path: Path, root_path: Path, enable_ocr: bool, ocr_engine: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    summary_lines = [
        "文件类型: 图片文件",
        f"文件名: {file_path.name}",
    ]
    ocr_text, ocr_metadata = extract_image_ocr_text(file_path, ocr_engine)
    if enable_ocr and ocr_text:
        summary_lines.append("OCR 状态: 已启用并提取到图片文字内容。")
        summary_lines.append(f"OCR 文本摘要: {trim_text(ocr_text, 500)}")
    elif enable_ocr:
        summary_lines.append(f"OCR 状态: 已启用，但未提取到可用文字。({ocr_metadata.get('ocr_status', 'unknown')})")
    else:
        summary_lines.append("OCR 状态: 未配置，当前仅建立图片文件级索引，不提取图片文字内容。")
    summary_lines.append("使用方式: 召回后优先返回文件路径；如需分析图片内容，请后续补充 OCR 或多模态描述流程。")
    metadata = {
        "index_mode": "file_summary",
        "content_granularity": "image_file_summary",
        "ocr_enabled": "true" if enable_ocr else "false",
        "ocr_text_preview": trim_text(ocr_text, 200) if ocr_text else "",
    }
    metadata.update(ocr_metadata)
    metadata.update(build_folder_metadata(file_path, root_path))
    return "\n".join(summary_lines), metadata


def build_file_summary(file_path: Path, root_path: Path, enable_ocr: bool, ocr_engine: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    asset_kind = detect_asset_kind(file_path)
    if asset_kind == "excel_asset":
        return summarize_excel_file(file_path, root_path)
    if asset_kind == "gis_asset":
        return summarize_gis_file(file_path, root_path)
    if asset_kind == "image_asset":
        return summarize_image_file(file_path, root_path, enable_ocr, ocr_engine)
    raise ValueError(f"不支持的文件摘要类型: {file_path}")


def calculate_file_hash(file_path: Path) -> str:
    """计算文件/目录哈希，用于增量更新去重。"""
    hasher = hashlib.md5()

    if file_path.is_dir():
        hasher.update(str(file_path).encode("utf-8", errors="ignore"))
        for child in sorted(file_path.rglob("*")):
            relative = str(child.relative_to(file_path)).replace("\\", "/")
            hasher.update(relative.encode("utf-8", errors="ignore"))
            try:
                stat = child.stat()
                hasher.update(str(int(stat.st_mtime)).encode("utf-8"))
                if child.is_file():
                    hasher.update(str(stat.st_size).encode("utf-8"))
            except OSError:
                continue
        return hasher.hexdigest()

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
    """递归查找所有支持的知识文件/目录资产。"""
    docs = []
    for ext in SUPPORTED_EXTENSIONS:
        for candidate in input_dir.rglob(f"*{ext}"):
            if ext == ".gdb":
                if candidate.is_dir():
                    docs.append(candidate)
                continue
            if candidate.is_file():
                docs.append(candidate)
    return sorted(set(docs))


def build_knowledge_base(
    input_dir: str,
    output_dir: str = "./vector_db",
    chunk_size: int = 300,
    chunk_overlap: int = 100,
    embedding_model: str = "BAAI/bge-base-zh-v1.5",
    incremental: bool = True,
    dry_run: bool = False,
    enable_ocr: bool = False,
):
    """
    批量构建向量数据库主流程

    Args:
        input_dir: 文档目录（递归扫描所有支持的知识文件）
        output_dir: 向量库持久化目录
        chunk_size: 分块大小，默认 300 字符
        chunk_overlap: 分块重叠，默认 100 字符（33% 重叠率）
        embedding_model: Embedding 模型名称
        incremental: 启用增量更新（跳过未修改文件）
        dry_run: 试运行，只统计不入库
        enable_ocr: 是否启用图片 OCR 和扫描 PDF OCR 降级
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
    logger.info("向量库      : Chroma（本地持久化，适合当前单机项目资料检索场景）")
    logger.info(f"分块参数   : size={chunk_size}, overlap={chunk_overlap}")
    logger.info(f"增量更新   : {'是' if incremental else '否（全量重建）'}")
    logger.info(f"图片 OCR   : {'启用' if enable_ocr else '关闭'}")
    if dry_run:
        logger.info("【试运行模式】只统计，不入库")

    # ── 1. 扫描文档 ──────────────────────────────────────────
    all_docs = find_documents(input_path)
    if not all_docs:
        logger.warning(f"未找到支持的文件，支持格式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
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
    ocr_engine = load_ocr_engine(enable_ocr)

    # ── 4. 批量入库 ───────────────────────────────────────────
    logger.info("\n开始处理文档...")
    success, failed = 0, []
    ingest_stats: Dict[str, int] = defaultdict(int)

    for i, (doc_path, file_key, file_hash) in enumerate(to_process, 1):
        logger.info(f"[{i}/{len(to_process)}] {doc_path.name}")
        try:
            source_metadata = build_common_metadata(doc_path, input_path, file_hash)
            # 同一路径重建前先清理旧索引，避免重复 chunk 残留。
            store.delete_by_metadata({"source": str(doc_path)})

            asset_kind = detect_asset_kind(doc_path)
            effective_metadata: Dict[str, str] = {**source_metadata}
            if asset_kind == "text_document":
                effective_metadata = add_text_document_with_fallback(
                    store=store,
                    file_path=doc_path,
                    source_metadata=source_metadata,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    enable_ocr=enable_ocr,
                    ocr_engine=ocr_engine,
                )
            else:
                summary_text, extra_metadata = build_file_summary(doc_path, input_path, enable_ocr, ocr_engine)
                effective_metadata = {
                    **source_metadata,
                    **extra_metadata,
                }
                store.add_text(
                    summary_text,
                    metadata=effective_metadata,
                    chunk_size=max(chunk_size, 500),
                    chunk_overlap=0,
                )

            processed_index[file_key] = {
                "hash": file_hash,
                "imported_at": datetime.now().isoformat(),
                "asset_kind": asset_kind,
            }

            ingest_stats[f"asset_kind::{asset_kind}"] += 1
            suffix = doc_path.suffix.lower()
            if suffix == ".pdf":
                pdf_ingest_mode = str(effective_metadata.get("pdf_ingest_mode", "") or "").strip()
                if pdf_ingest_mode:
                    ingest_stats[f"pdf_mode::{pdf_ingest_mode}"] += 1
            if suffix == ".docx":
                docx_ingest_mode = str(effective_metadata.get("docx_ingest_mode", "") or "").strip()
                if docx_ingest_mode:
                    ingest_stats[f"docx_mode::{docx_ingest_mode}"] += 1
                ingest_stats["docx_image_count_total"] += int(effective_metadata.get("docx_image_count", "0") or 0)
                ingest_stats["docx_image_ocr_success_total"] += int(effective_metadata.get("docx_image_ocr_success_count", "0") or 0)

            if asset_kind == "image_asset":
                if str(effective_metadata.get("ocr_status", "") or "") == "success":
                    ingest_stats["image_ocr_success_files"] += 1

            success += 1
        except Exception as e:
            logger.error(f"  ✗ 失败: {e}")
            failed.append(doc_path.name)

    # ── 5. 保存索引 & 汇总 ───────────────────────────────────
    save_index(index_path, processed_index)

    logger.info("\n" + "=" * 55)
    logger.info(f"完成！成功: {success}  失败: {len(failed)}")
    logger.info(f"向量库总文档块数: {store.get_document_count()}")
    logger.info("建库统计：")
    logger.info(f"  文本类文件       : {ingest_stats.get('asset_kind::text_document', 0)}")
    logger.info(f"  Excel/表格资产   : {ingest_stats.get('asset_kind::excel_asset', 0)}")
    logger.info(f"  GIS 资产         : {ingest_stats.get('asset_kind::gis_asset', 0)}")
    logger.info(f"  图片资产         : {ingest_stats.get('asset_kind::image_asset', 0)}")
    logger.info(f"  Word 正文入库     : {ingest_stats.get('docx_mode::text_only', 0)}")
    logger.info(f"  Word 图文混合入库 : {ingest_stats.get('docx_mode::text_with_image_ocr', 0)}")
    logger.info(f"  Word 图片总数    : {ingest_stats.get('docx_image_count_total', 0)}")
    logger.info(f"  Word 图片 OCR 成功: {ingest_stats.get('docx_image_ocr_success_total', 0)}")
    logger.info(f"  图片文件 OCR 成功 : {ingest_stats.get('image_ocr_success_files', 0)}")
    logger.info(f"  PDF 原生文本     : {ingest_stats.get('pdf_mode::native_text', 0)}")
    logger.info(f"  PDF OCR 降级     : {ingest_stats.get('pdf_mode::ocr_fallback', 0)}")
    logger.info(f"  PDF 占位摘要     : {ingest_stats.get('pdf_mode::empty_placeholder', 0)}")
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
    parser.add_argument("--enable-ocr", action="store_true", help="启用图片 OCR 提取；若未安装 OCR 依赖会自动降级为仅文件级索引")

    args = parser.parse_args()
    build_knowledge_base(
        input_dir=args.input,
        output_dir=args.output,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embedding_model=args.embedding_model,
        incremental=not args.no_incremental,
        dry_run=args.dry_run,
        enable_ocr=args.enable_ocr,
    )


if __name__ == "__main__":
    main()
