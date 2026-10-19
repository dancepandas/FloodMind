"""CLI: 生成 FloodMind 项目知识图谱。

用法：
    python -m scripts.knowledge_graph [--root .] [--out docs/knowledge-graph/graph]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .model import Graph
from .py_analyzer import analyze_python
from .report import write_reports
from .ts_analyzer import analyze_typescript


def build(root: Path, *, include_typescript: bool = True) -> Graph:
    graph = Graph()
    analyze_python(root, graph)
    if include_typescript:
        analyze_typescript(root, graph)
    return graph


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="从源码静态生成 FloodMind 项目知识图谱"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="仓库根目录（默认：脚本所在仓库）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_root / "docs" / "knowledge-graph" / "graph",
        help="输出目录",
    )
    parser.add_argument(
        "--no-ts",
        action="store_true",
        help="跳过 TypeScript/JavaScript 分析",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out = args.out.resolve()
    graph = build(root, include_typescript=not args.no_ts)
    metrics = write_reports(graph, out, root=str(root))

    summary = metrics["summary"]
    print(f"knowledge graph written to {out}")
    print(
        "  files={files} symbols={symbols} edges={edges} "
        "import_edges={import_edges} call_edges={call_edges} "
        "cycles={cycles} unresolved={unresolved_imports}".format(**summary)
    )
    print(f"  visualization: {out / 'graph.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
