"""
将 Word 文档 (.docx) 转换为 PDF

使用 LibreOffice (soffice) 进行转换，Windows 也可回退到 comtypes (MS Word)。
与 create_docx.py 配合使用：先用 create_docx.py 生成 .docx，再用本脚本转为 PDF。

用法:
    python convert_docx_to_pdf.py --input report.docx
    python convert_docx_to_pdf.py --input report.docx --output report.pdf
    python convert_docx_to_pdf.py --input report.docx --output_dir ./output
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _find_soffice() -> str | None:
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
    found = shutil.which("soffice")
    if found:
        return found
    return None


def _convert_with_soffice(input_path: str, output_dir: str) -> str | None:
    soffice = _find_soffice()
    if not soffice:
        return None

    env = os.environ.copy()
    env["SAL_USE_VCLPLUGIN"] = "svp"

    result = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", output_dir, input_path],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        return None

    pdf_name = Path(input_path).stem + ".pdf"
    pdf_path = os.path.join(output_dir, pdf_name)
    return pdf_path if os.path.exists(pdf_path) else None


def _convert_with_comtypes(input_path: str, output_path: str) -> bool:
    if platform.system() != "Windows":
        return False

    try:
        import comtypes.client
    except ImportError:
        return False

    try:
        word = comtypes.client.CreateObject("Word.Application")
        word.Visible = False

        doc = word.Documents.Open(os.path.abspath(input_path))
        doc.SaveAs(os.path.abspath(output_path), FileFormat=17)
        doc.Close()
        word.Quit()
        return True
    except Exception:
        try:
            word.Quit()
        except Exception:
            pass
        return False


def _convert_with_docx2pdf(input_path: str, output_path: str) -> bool:
    try:
        from docx2pdf import convert
        convert(input_path, output_path)
        return os.path.exists(output_path)
    except ImportError:
        return False
    except Exception:
        return False


def convert_docx_to_pdf(input_path: str, output_path: str | None = None, output_dir: str | None = None) -> dict:
    input_file = Path(input_path)
    if not input_file.exists():
        return {"success": False, "error": f"输入文件不存在: {input_path}"}
    if input_file.suffix.lower() != ".docx":
        return {"success": False, "error": f"输入文件必须是 .docx 格式，当前: {input_file.suffix}"}

    if output_path:
        pdf_file = Path(output_path)
        pdf_file.parent.mkdir(parents=True, exist_ok=True)
    elif output_dir:
        pdf_file = Path(output_dir) / (input_file.stem + ".pdf")
        pdf_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        pdf_file = input_file.with_suffix(".pdf")

    pdf_path = str(pdf_file)

    with tempfile.TemporaryDirectory() as tmpdir:
        soffice_result = _convert_with_soffice(str(input_file), tmpdir)
        if soffice_result:
            shutil.copy2(soffice_result, pdf_path)
            if os.path.exists(pdf_path):
                return {"success": True, "file": pdf_path, "name": pdf_file.name}

    if _convert_with_comtypes(str(input_file), pdf_path):
        if os.path.exists(pdf_path):
            return {"success": True, "file": pdf_path, "name": pdf_file.name}

    if _convert_with_docx2pdf(str(input_file), pdf_path):
        if os.path.exists(pdf_path):
            return {"success": True, "file": pdf_path, "name": pdf_file.name}

    return {
        "success": False,
        "error": "转换失败：未找到可用的转换工具。请安装 LibreOffice 或 Microsoft Word。",
        "suggestions": [
            "安装 LibreOffice: https://www.libreoffice.org/download/",
            "Windows 安装 comtypes: pip install comtypes",
            "Windows 安装 docx2pdf: pip install docx2pdf",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="将 Word 文档 (.docx) 转换为 PDF")
    parser.add_argument("--input", required=True, help="输入的 .docx 文件路径")
    parser.add_argument("--output", default=None, help="输出的 PDF 文件路径")
    parser.add_argument("--output_dir", default=None, help="输出目录（与 --output 互斥）")
    args = parser.parse_args()

    if args.output and args.output_dir:
        print("错误: --output 和 --output_dir 不能同时使用")
        sys.exit(1)

    result = convert_docx_to_pdf(args.input, args.output, args.output_dir)

    if result["success"]:
        print(f"PDF转换成功: {result['name']}")
        print(f"路径: {result['file']}")
    else:
        print(f"错误: {result['error']}")
        if "suggestions" in result:
            print("建议:")
            for s in result["suggestions"]:
                print(f"  - {s}")
        sys.exit(1)


if __name__ == "__main__":
    main()
