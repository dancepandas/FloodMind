#!/usr/bin/env python3

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _parse_json(value: str, name: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"错误：{name} 不是合法的 JSON: {exc}") from exc


def _normalize_rows(rows, columns):
    if not isinstance(rows, list):
        raise SystemExit("错误：rows 必须是 JSON 数组")

    if not rows:
        return columns or [], []

    first = rows[0]
    if isinstance(first, dict):
        ordered_columns = columns or list(first.keys())
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                raise SystemExit("错误：对象数组中包含非对象元素")
            missing = [col for col in ordered_columns if col not in row]
            if missing:
                raise SystemExit(f"错误：rows 缺少列: {missing}")
            normalized.append([row.get(col) for col in ordered_columns])
        return ordered_columns, normalized

    if isinstance(first, list):
        if not columns:
            raise SystemExit("错误：rows 为二维数组时必须提供 columns")
        for row in rows:
            if not isinstance(row, list):
                raise SystemExit("错误：二维数组中包含非数组元素")
            if len(row) != len(columns):
                raise SystemExit("错误：rows 中存在与 columns 长度不一致的数据行")
        return columns, rows

    raise SystemExit("错误：rows 仅支持对象数组或二维数组")


def _column_letter(index: int) -> str:
    result = []
    while index > 0:
        index, rem = divmod(index - 1, 26)
        result.append(chr(65 + rem))
    return "".join(reversed(result))


def _xml_cell(value, ref: str, style_id: int) -> str:
    if value is None:
        return f'<c r="{ref}" s="{style_id}"/>'

    if isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}" t="b"><v>{1 if value else 0}</v></c>'

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}"><v>{value}</v></c>'

    if isinstance(value, (datetime, date)):
        text = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    else:
        text = str(value)

    return (
        f'<c r="{ref}" s="{style_id}" t="inlineStr">'
        f'<is><t>{escape(text)}</t></is>'
        f'</c>'
    )


def _build_sheet_xml(columns, rows):
    all_rows = [columns] + rows
    max_lengths = [len(str(col)) for col in columns] if columns else []

    row_xml = []
    for row_idx, row in enumerate(all_rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            text_len = len("" if value is None else str(value))
            if col_idx > len(max_lengths):
                max_lengths.append(text_len)
            else:
                max_lengths[col_idx - 1] = max(max_lengths[col_idx - 1], text_len)
            ref = f"{_column_letter(col_idx)}{row_idx}"
            style_id = 1 if row_idx == 1 else 0
            cells.append(_xml_cell(value, ref, style_id))
        row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    cols_xml = []
    for idx, length in enumerate(max_lengths, start=1):
        width = min(max(length + 2, 10), 40)
        cols_xml.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')

    dimension_ref = "A1"
    if columns:
        dimension_ref = f"A1:{_column_letter(len(columns))}{len(all_rows)}"

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="{dimension_ref}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>{''.join(cols_xml)}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
</worksheet>
'''


def _sanitize_sheet_name(sheet_name: str) -> str:
    cleaned = "".join(ch for ch in sheet_name if ch not in '[]:*?/\\')[:31].strip()
    return cleaned or "Sheet1"


def _write_xlsx(path: Path, sheet_name: str, columns, rows):
    workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Arial"/><family val="2"/></font>
    <font><b/><sz val="11"/><name val="Arial"/><family val="2"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
  </fills>
  <borders count="1">
    <border><left/><right/><top/><bottom/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>
'''

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
''',
        )
        zf.writestr(
            "_rels/.rels",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
''',
        )
        zf.writestr(
            "docProps/core.xml",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>FloodAgent</dc:creator>
  <cp:lastModifiedBy>FloodAgent</cp:lastModifiedBy>
</cp:coreProperties>
''',
        )
        zf.writestr(
            "docProps/app.xml",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>FloodAgent</Application>
</Properties>
''',
        )
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
''',
        )
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(columns, rows))


def main():
    parser = argparse.ArgumentParser(description="创建 Excel 文件")
    parser.add_argument("--output", required=True, help="输出文件绝对路径")
    parser.add_argument("--rows", required=True, help="JSON 数组，支持对象数组或二维数组")
    parser.add_argument("--columns", help="列名 JSON 数组，可选")
    parser.add_argument("--sheet_name", default="Sheet1", help="工作表名称")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    columns = _parse_json(args.columns, "columns") if args.columns else None
    if columns is not None and not isinstance(columns, list):
        raise SystemExit("错误：columns 必须是 JSON 数组")

    rows = _parse_json(args.rows, "rows")
    final_columns, final_rows = _normalize_rows(rows, columns)
    final_sheet_name = _sanitize_sheet_name(args.sheet_name)
    _write_xlsx(output_path, final_sheet_name, final_columns, final_rows)

    print(f"Excel 文件已创建: {output_path}")
    print(f"工作表: {final_sheet_name}")
    print(f"数据行数: {len(final_rows)}")
    print(f"列数: {len(final_columns)}")


if __name__ == "__main__":
    main()
