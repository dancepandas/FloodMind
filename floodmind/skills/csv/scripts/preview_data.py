"""
CSV数据预览脚本 - 仅读取文件前N行用于预览

安全措施：
1. 默认只读取5行
2. 最大限制100行（防止内存溢出）
3. 文件大小限制50MB
4. 不加载完整数据到内存
"""

import argparse
import csv
import os
import sys
from pathlib import Path


def get_file_sizeKB(path: str) -> float:
    return os.path.getsize(path) / 1024


def preview_csv(file_path: str, n_rows: int = 5, delimiter: str = ",", encoding: str = "utf-8") -> dict:
    rows = []
    total_rows = 0

    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for i, row in enumerate(reader):
            total_rows += 1
            if i < n_rows:
                rows.append(row)
            elif i >= 10000:
                break

    return {
        "total_rows": total_rows if total_rows <= 10000 else f">10000 (文件过大，仅统计前10000行)",
        "preview_rows": len(rows),
        "columns": len(rows[0]) if rows else 0,
        "headers": rows[0] if rows else [],
        "data": rows[1:] if len(rows) > 1 else [],
        "delimiter": delimiter,
    }


def main():
    parser = argparse.ArgumentParser(description="CSV数据预览 - 仅读取前N行")
    parser.add_argument("--file_path", required=True, help="文件路径")
    parser.add_argument("--n_rows", type=int, default=5, help="预览行数，默认5，最大100")
    parser.add_argument("--delimiter", default=",", help="分隔符，默认逗号")
    parser.add_argument("--encoding", default="utf-8", help="文件编码")

    args = parser.parse_args()

    file_path = Path(args.file_path)

    if not file_path.exists():
        print(f"错误：文件不存在: {file_path}")
        sys.exit(1)

    size_kb = get_file_sizeKB(file_path)
    if size_kb > 51200:
        print(f"错误：文件过大 ({size_kb:.1f}KB)，最大支持 50MB")
        sys.exit(1)

    n_rows = min(max(1, args.n_rows), 100)

    try:
        result = preview_csv(str(file_path), n_rows, args.delimiter, args.encoding)
    except Exception as e:
        print(f"错误：读取文件失败: {e}")
        sys.exit(1)

    print("=" * 60)
    print(f"文件: {file_path.name}")
    print(f"总行数: {result['total_rows']}")
    print(f"预览行数: {result['preview_rows']}")
    print(f"列数: {result['columns']}")
    print(f"分隔符: {repr(result['delimiter'])}")
    print("=" * 60)

    print(f"\n【表头】")
    print(" | ".join(str(h)[:30] for h in result["headers"]))

    print(f"\n【数据预览（前{result['preview_rows']-1}行）】")
    for row in result["data"]:
        print(" | ".join(str(c)[:30] for c in row))

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
