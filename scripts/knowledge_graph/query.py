"""查询已生成的知识图谱。

用法示例：
    python -m scripts.knowledge_graph.query floodmind/agent/native/executor.py
    python -m scripts.knowledge_graph.query NativeAgentExecutor
    python -m scripts.knowledge_graph.query floodmind/memory/dual_memory.py --in
    python -m scripts.knowledge_graph.query floodmind/tools/base_tools.py --symbols
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_DEFAULT_GRAPH = Path("docs/knowledge-graph/graph/graph.json")


def load_graph(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"graph not found: {path}. Run `python -m scripts.knowledge_graph` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in graph.get("nodes", [])}


def _find_nodes(graph: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Match by file path, qualified name, or bare symbol name."""
    normalized = query.replace("\\", "/").strip()
    nodes = graph.get("nodes", [])

    file_matches = [
        node
        for node in nodes
        if node.get("kind") == "file"
        and (node.get("path") == normalized or node.get("id") == normalized)
    ]
    if file_matches:
        return file_matches

    qualified = [
        node
        for node in nodes
        if node.get("qualified_name") == normalized
    ]
    if qualified:
        return qualified

    return [
        node
        for node in nodes
        if (node.get("name") or "") == normalized
    ]


def _file_id_for(path: str) -> str:
    return f"file:{path}"


def describe(
    graph: dict[str, Any],
    query: str,
    *,
    show_in: bool,
    show_out: bool,
    show_symbols: bool,
) -> str:
    nodes = _node_map(graph)
    matches = _find_nodes(graph, query)
    if not matches:
        return f"no node matched: {query}"

    lines: list[str] = []
    for node in matches[:10]:
        lines.append(f"{node['kind']}: {node.get('qualified_name') or node['id']}")
        if node.get("path"):
            lines.append(f"  path : {node['path']}")
            lines.append(f"  layer: {node.get('layer', '')}")
        if node.get("line"):
            lines.append(f"  line : {node['line']}")

    # File nodes: imports and defined symbols.
    file_nodes = [n for n in matches if n["kind"] == "file"]
    if file_nodes:
        target = file_nodes[0]["path"]
        target_id = _file_id_for(target)
        if show_out:
            outs = [
                e for e in graph["edges"]
                if e["kind"] == "imports" and e["src"] == target_id
            ]
            lines.append("")
            lines.append(f"imports ({len(outs)}):")
            for edge in sorted(outs, key=lambda e: e["dst"]):
                dst = nodes.get(edge["dst"], {})
                lines.append(f"  -> {dst.get('path', edge['dst'])}")
        if show_in:
            ins = [
                e for e in graph["edges"]
                if e["kind"] == "imports" and e["dst"] == target_id
            ]
            lines.append("")
            lines.append(f"imported by ({len(ins)}):")
            for edge in sorted(ins, key=lambda e: e["src"]):
                src = nodes.get(edge["src"], {})
                lines.append(f"  <- {src.get('path', edge['src'])}")
        if show_symbols:
            defs = [
                e for e in graph["edges"]
                if e["kind"] == "defines" and e["src"] == target_id
            ]
            lines.append("")
            lines.append(f"symbols ({len(defs)}):")
            for edge in sorted(defs, key=lambda e: nodes.get(e["dst"], {}).get("line", 0)):
                sym = nodes.get(edge["dst"], {})
                lines.append(
                    f"  {sym.get('kind', '')} {sym.get('name', '')}"
                    f" :{sym.get('line', 0)}"
                )
        return "\n".join(lines)

    # Symbol nodes: callers and callees.
    symbol_ids = {n["id"] for n in matches}
    # A class match should also cover calls made by its methods.
    roots = [
        nodes[sid].get("qualified_name", "")
        for sid in symbol_ids
        if nodes.get(sid, {}).get("kind") == "class"
    ]
    if roots:
        for node in graph.get("nodes", []):
            qualified = node.get("qualified_name", "")
            if any(qualified.startswith(root + ".") for root in roots if root):
                symbol_ids.add(node["id"])
    if show_in:
        callers = [
            e for e in graph["edges"]
            if e["kind"] == "calls" and e["dst"] in symbol_ids
        ]
        lines.append("")
        lines.append(f"callers ({len(callers)}):")
        for edge in callers:
            src = nodes.get(edge["src"], {})
            lines.append(f"  <- {src.get('qualified_name', edge['src'])}")
    if show_out:
        callees = [
            e for e in graph["edges"]
            if e["kind"] == "calls" and e["src"] in symbol_ids
        ]
        lines.append("")
        lines.append(f"calls ({len(callees)}):")
        for edge in callees:
            dst = nodes.get(edge["dst"], {})
            lines.append(f"  -> {dst.get('qualified_name', edge['dst'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the FloodMind knowledge graph")
    parser.add_argument("query", help="file path, qualified name, or symbol name")
    parser.add_argument("--graph", type=Path, default=_DEFAULT_GRAPH)
    parser.add_argument("--in", dest="show_in", action="store_true", help="show incoming edges")
    parser.add_argument("--out", dest="show_out", action="store_true", help="show outgoing edges")
    parser.add_argument("--symbols", action="store_true", help="show defined symbols")
    parser.add_argument("--all", action="store_true", help="same as --in --out --symbols")
    args = parser.parse_args(argv)

    show_in = args.show_in or args.all
    show_out = args.show_out or args.all
    show_symbols = args.symbols or args.all
    if not (show_in or show_out or show_symbols):
        show_in = show_out = True

    graph = load_graph(args.graph)
    print(
        describe(
            graph,
            args.query,
            show_in=show_in,
            show_out=show_out,
            show_symbols=show_symbols,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
