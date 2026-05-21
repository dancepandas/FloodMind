"""
创建Word文档报告

支持Markdown格式内容，自动转换为Word文档。
样式规范：
- 正文：宋体小四号(12pt)，Times New Roman小四号，两端对齐，单倍行距，首行缩进二字符
- 题目：黑体小二号(18pt)，段后1行
- 一级标题：黑体四号(14pt)，段后1行
- 二级标题：黑体小四号(12pt)，段后1行
- 三级标题：宋体小四号(12pt)，段后1行
- 表格表名/图名：中文宋体五号(10.5pt)，英文Times New Roman五号
- 表格内字体：中文宋体五号(10.5pt)，英文Times New Roman五号
"""

import argparse
import json
import sys
from pathlib import Path

from docx.shared import Pt

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}

FONT_CN = '宋体'
FONT_EN = 'Times New Roman'
FONT_HEITI = '黑体'

SIZE_XIAO_ER = 18       # 小二号 18pt
SIZE_SI_HAO = 14         # 四号 14pt
SIZE_XIAO_SI = 12        # 小四号 12pt
SIZE_WU_HAO = 10.5       # 五号 10.5pt


def _set_run_font(run, font_cn: str = FONT_CN, font_en: str = FONT_EN, size: float | None = None, bold: bool | None = None):
    from docx.oxml.ns import qn

    run.font.name = font_en
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_cn)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def _set_paragraph_spacing(paragraph, space_after_pt: float | None = None, line_spacing: float | None = None, first_line_indent_chars: int | None = None):
    from docx.shared import Cm

    pf = paragraph.paragraph_format
    if space_after_pt is not None:
        pf.space_after = Pt(space_after_pt)
    if line_spacing is not None:
        pf.line_spacing = line_spacing
    if first_line_indent_chars is not None:
        pf.first_line_indent = Pt(first_line_indent_chars * SIZE_XIAO_SI)


def _apply_doc_styles(doc):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    normal_style = doc.styles['Normal']
    normal_style.font.name = FONT_EN
    normal_style._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_CN)
    normal_style.font.size = Pt(SIZE_XIAO_SI)
    pf = normal_style.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf.line_spacing = 1.0
    pf.first_line_indent = Pt(2 * SIZE_XIAO_SI)
    pf.space_after = Pt(0)

    heading_configs = [
        (1, FONT_HEITI, FONT_EN, SIZE_SI_HAO, 12),
        (2, FONT_HEITI, FONT_EN, SIZE_XIAO_SI, 12),
        (3, FONT_CN, FONT_EN, SIZE_XIAO_SI, 12),
    ]
    for level, cn_font, en_font, size, space_after in heading_configs:
        style = doc.styles[f'Heading {level}']
        style.font.name = en_font
        style._element.rPr.rFonts.set(qn('w:eastAsia'), cn_font)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = None
        pf = style.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(space_after)
        pf.first_line_indent = Pt(0)
        pf.line_spacing = 1.0

    if 'Title' in doc.styles:
        title_style = doc.styles['Title']
        title_style.font.name = FONT_EN
        title_style._element.rPr.rFonts.set(qn('w:eastAsia'), FONT_HEITI)
        title_style.font.size = Pt(SIZE_XIAO_ER)
        title_style.font.bold = True
        title_style.font.color.rgb = None
        pf = title_style.paragraph_format
        pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pf.space_before = Pt(0)
        pf.space_after = Pt(12)
        pf.first_line_indent = Pt(0)
        pf.line_spacing = 1.0


def _is_markdown_table_row(line: str) -> bool:
    return '|' in line and line.strip().startswith('|')


def _is_markdown_table_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(cell and set(cell) <= {'-', ':', ' '} for cell in cells)


def _para_has_image(paragraph) -> bool:
    from docx.oxml.ns import qn

    for run in paragraph.runs:
        if run._element.findall(qn('w:drawing')):
            return True
    return False


def _append_table_caption(doc, caption: str):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_paragraph_spacing(p, space_after_pt=3, line_spacing=1.0)
    p.paragraph_format.first_line_indent = Pt(0)
    run = p.add_run(caption)
    _set_run_font(run, font_cn=FONT_CN, font_en=FONT_EN, size=SIZE_WU_HAO)


def _append_figure_caption(doc, caption: str):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_paragraph_spacing(p, space_after_pt=6, line_spacing=1.0)
    p.paragraph_format.first_line_indent = Pt(0)
    run = p.add_run(caption)
    _set_run_font(run, font_cn=FONT_CN, font_en=FONT_EN, size=SIZE_WU_HAO)


def _append_markdown_table(doc, table_rows: list[list[str]], caption: str | None = None):
    if not table_rows:
        return

    filtered_rows = [row for row in table_rows if not _is_markdown_table_separator(row)]
    if len(filtered_rows) < 2:
        return

    if caption:
        _append_table_caption(doc, caption)

    headers = filtered_rows[0]
    data_rows = filtered_rows[1:]

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    table.autofit = True

    hdr_cells = table.rows[0].cells
    for i, header in enumerate(headers):
        paragraph = hdr_cells[i].paragraphs[0]
        paragraph.alignment = 1
        _set_paragraph_spacing(paragraph, line_spacing=1.0)
        paragraph.paragraph_format.first_line_indent = Pt(0)
        run = paragraph.add_run(str(header))
        _set_run_font(run, font_cn=FONT_HEITI, font_en=FONT_EN, size=SIZE_WU_HAO, bold=True)

    for row_data in data_rows:
        row_cells = table.add_row().cells
        for i, cell in enumerate(row_data):
            if i >= len(row_cells):
                continue
            paragraph = row_cells[i].paragraphs[0]
            _set_paragraph_spacing(paragraph, line_spacing=1.0)
            paragraph.paragraph_format.first_line_indent = Pt(0)
            run = paragraph.add_run(str(cell))
            _set_run_font(run, font_cn=FONT_CN, font_en=FONT_EN, size=SIZE_WU_HAO)

    doc.add_paragraph()


def _append_markdown_paragraph(doc, text: str):
    import re

    paragraph = doc.add_paragraph()
    _set_paragraph_spacing(paragraph, line_spacing=1.0, first_line_indent_chars=2)

    cursor = 0
    for match in re.finditer(r'(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)', text):
        if match.start() > cursor:
            run = paragraph.add_run(text[cursor:match.start()])
            _set_run_font(run)

        token = match.group(0)
        if token.startswith('**') and token.endswith('**'):
            run = paragraph.add_run(token[2:-2])
            _set_run_font(run, bold=True)
        elif token.startswith('*') and token.endswith('*'):
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run)
            run.italic = True
        else:
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, font_en='Consolas')

        cursor = match.end()

    if cursor < len(text):
        run = paragraph.add_run(text[cursor:])
        _set_run_font(run)


def _extract_image_path(line: str) -> str | None:
    import re

    markdown_match = re.match(r'^!\[([^\]]*)\]\((.+)\)$', line.strip())
    if markdown_match:
        alt_text = markdown_match.group(1).strip()
        image_path = markdown_match.group(2).strip()
        path = Path(image_path)
        if path.suffix.lower() in IMAGE_EXTENSIONS and path.exists():
            return str(path)
        return None

    candidate = line.strip().strip('`')
    path = Path(candidate)
    if path.suffix.lower() in IMAGE_EXTENSIONS and path.exists():
        return str(path)
    return None


def _extract_image_caption(line: str) -> str | None:
    import re

    markdown_match = re.match(r'^!\[([^\]]*)\]\((.+)\)$', line.strip())
    if markdown_match:
        alt_text = markdown_match.group(1).strip()
        return alt_text if alt_text else None
    return None


def _append_image(doc, image_path: str, caption: str | None = None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches

    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_paragraph_spacing(paragraph, space_after_pt=3, line_spacing=1.0)
    paragraph.paragraph_format.first_line_indent = Pt(0)
    run = paragraph.add_run()
    run.add_picture(image_path, width=Inches(6.2))

    if caption:
        _append_figure_caption(doc, caption)


def _add_heading_with_style(doc, text: str, level: int):
    heading = doc.add_heading(text, level=level if level <= 6 else 6)
    for run in heading.runs:
        if level == 1:
            _set_run_font(run, font_cn=FONT_HEITI, font_en=FONT_EN, size=SIZE_SI_HAO, bold=True)
        elif level == 2:
            _set_run_font(run, font_cn=FONT_HEITI, font_en=FONT_EN, size=SIZE_XIAO_SI, bold=True)
        elif level == 3:
            _set_run_font(run, font_cn=FONT_CN, font_en=FONT_EN, size=SIZE_XIAO_SI, bold=True)
        else:
            _set_run_font(run, font_cn=FONT_HEITI, font_en=FONT_EN, size=SIZE_XIAO_SI, bold=True)
    _set_paragraph_spacing(heading, space_after_pt=12, line_spacing=1.0)
    heading.paragraph_format.first_line_indent = Pt(0)
    return heading


def create_docx_from_markdown(content: str, output_path: str) -> dict:
    import re

    from docx import Document

    doc = Document()
    _apply_doc_styles(doc)

    lines = content.split('\n')
    in_table = False
    table_rows = []
    table_caption = None
    pending_figure_caption = None

    for line in lines:
        line = line.rstrip()

        if line.startswith('```'):
            continue

        image_path = _extract_image_path(line)
        if image_path:
            caption = _extract_image_caption(line) or pending_figure_caption
            pending_figure_caption = None
            _append_image(doc, image_path, caption)
            continue

        if _is_markdown_table_row(line):
            if not in_table:
                in_table = True
                table_rows = []
            cells = [c.strip() for c in line.split('|')[1:-1]]
            table_rows.append(cells)
            continue
        else:
            if in_table and table_rows:
                _append_markdown_table(doc, table_rows, table_caption)
                table_rows = []
                table_caption = None
                in_table = False

        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            _add_heading_with_style(doc, text, level)
            continue

        m = re.match(r'^\*\*([^*]+)\*\*$', line)
        if m:
            p = doc.add_paragraph()
            _set_paragraph_spacing(p, line_spacing=1.0, first_line_indent_chars=2)
            run = p.add_run(m.group(1))
            _set_run_font(run, bold=True)
            continue

        m = re.match(r'^\*([^*]+)\*$', line)
        if m:
            p = doc.add_paragraph()
            _set_paragraph_spacing(p, line_spacing=1.0, first_line_indent_chars=2)
            run = p.add_run(m.group(1))
            _set_run_font(run)
            run.italic = True
            continue

        m = re.match(r'^表\s*[\d.]+\s+.*$', line.strip())
        if m and not in_table:
            table_caption = line.strip()
            continue

        m = re.match(r'^图\s*[\d.]+\s+.*$', line.strip())
        if m:
            last_para = doc.paragraphs[-1] if doc.paragraphs else None
            if last_para and _para_has_image(last_para):
                _append_figure_caption(doc, line.strip())
            else:
                pending_figure_caption = line.strip()
            continue

        if pending_figure_caption:
            _append_figure_caption(doc, pending_figure_caption)
            pending_figure_caption = None

        if line.startswith('- ') or line.startswith('* '):
            p = doc.add_paragraph(style='List Bullet')
            run = p.add_run(line[2:])
            _set_run_font(run)
            _set_paragraph_spacing(p, line_spacing=1.0)
            continue

        m = re.match(r'^\d+\.\s+(.*)', line)
        if m:
            p = doc.add_paragraph(style='List Number')
            run = p.add_run(m.group(1))
            _set_run_font(run)
            _set_paragraph_spacing(p, line_spacing=1.0)
            continue

        if line.strip():
            _append_markdown_paragraph(doc, line)

    if pending_figure_caption:
        _append_figure_caption(doc, pending_figure_caption)

    if in_table and table_rows:
        _append_markdown_table(doc, table_rows, table_caption)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_file))

    return {
        "success": True,
        "file": str(output_file),
        "name": output_file.name
    }


def create_docx_from_json(content: str, output_path: str) -> dict:
    import json

    from docx import Document

    data = json.loads(content)
    doc = Document()
    _apply_doc_styles(doc)

    if 'title' in data:
        heading = doc.add_heading(data['title'], 0)
        for run in heading.runs:
            _set_run_font(run, font_cn=FONT_HEITI, font_en=FONT_EN, size=SIZE_XIAO_ER, bold=True)
        _set_paragraph_spacing(heading, space_after_pt=12, line_spacing=1.0)
        heading.paragraph_format.first_line_indent = Pt(0)

    if 'sections' in data:
        for section in data['sections']:
            section_type = section.get('type')
            text = section.get('text', '')
            level = section.get('level', 1)

            if section_type == 'heading':
                _add_heading_with_style(doc, text, level)
            elif section_type == 'paragraph':
                _append_markdown_paragraph(doc, text)
            elif section_type == 'bullet_list':
                for item in section.get('items', []):
                    p = doc.add_paragraph(style='List Bullet')
                    run = p.add_run(item)
                    _set_run_font(run)
                    _set_paragraph_spacing(p, line_spacing=1.0)
            elif section_type == 'numbered_list':
                for item in section.get('items', []):
                    p = doc.add_paragraph(style='List Number')
                    run = p.add_run(item)
                    _set_run_font(run)
                    _set_paragraph_spacing(p, line_spacing=1.0)
            elif section_type == 'table':
                headers = section.get('headers', [])
                rows = section.get('rows', [])
                caption = section.get('caption')
                _append_markdown_table(doc, [headers] + rows, caption)
            elif section_type == 'image':
                image_path = section.get('path') or section.get('image_path')
                if image_path and Path(image_path).exists():
                    _append_image(doc, image_path, section.get('caption'))
            elif section_type == 'table_caption':
                _append_table_caption(doc, text)
            elif section_type == 'figure_caption':
                _append_figure_caption(doc, text)
            elif section_type == 'page_break':
                doc.add_page_break()

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_file))

    return {
        "success": True,
        "file": str(output_file),
        "name": output_file.name
    }


def main():
    parser = argparse.ArgumentParser(description="创建Word文档报告")
    parser.add_argument("--content", required=True, help="文档内容文件路径（.md 或 .json）")
    parser.add_argument("--output_path", required=True, help="输出文件名称")
    parser.add_argument("--format", default="auto", choices=["auto", "markdown", "json"], help="内容格式")

    args = parser.parse_args()

    content_path = Path(args.content.strip())
    if not content_path.exists():
        print(f"错误: 内容文件不存在: {content_path}")
        sys.exit(1)

    try:
        content = content_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"错误: 读取内容文件失败: {e}")
        sys.exit(1)

    if args.format == "json" or (args.format == "auto" and content_path.suffix.lower() == '.json') or (args.format == "auto" and content.lstrip().startswith('{')):
        result = create_docx_from_json(content, args.output_path)
    else:
        result = create_docx_from_markdown(content, args.output_path)

    if result["success"]:
        print(f"Word文档创建成功: {result['name']}")
        print(f"路径: {result['file']}")
    else:
        print(f"错误: 创建失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
