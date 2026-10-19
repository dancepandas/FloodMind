"""把图数据聚合为可读报告：指标、Mermaid 图、循环依赖与未解析导入。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .layers import all_layer_titles, layer_title
from .model import Edge, Graph, Node
from .subsystems import render_semantic_mermaid, semantic_graph
from .viz import write_visualization


def _node_index(graph: Graph) -> dict[str, Node]:
    return {node.id: node for node in graph.nodes}


def _file_import_edges(graph: Graph) -> list[tuple[str, str]]:
    index = _node_index(graph)
    edges: list[tuple[str, str]] = []
    for edge in graph.edges:
        if edge.kind != "imports":
            continue
        src, dst = index.get(edge.src), index.get(edge.dst)
        if src and dst and src.kind == "file" and dst.kind == "file":
            edges.append((src.path, dst.path))
    return edges


def _tarjan(nodes: Iterable[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    components: list[list[str]] = []

    def visit(vertex: str) -> None:
        index[vertex] = low[vertex] = counter[0]
        counter[0] += 1
        stack.append(vertex)
        on_stack.add(vertex)
        for neighbour in adjacency.get(vertex, ()):  # type: ignore[arg-type]
            if neighbour not in index:
                visit(neighbour)
                low[vertex] = min(low[vertex], low[neighbour])
            elif neighbour in on_stack:
                low[vertex] = min(low[vertex], index[neighbour])
        if low[vertex] == index[vertex]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == vertex:
                    break
            if len(component) > 1:
                components.append(component)

    for vertex in nodes:
        if vertex not in index:
            visit(vertex)
    return components


def build_metrics(graph: Graph) -> dict[str, Any]:
    index = _node_index(graph)
    path_index = {
        node.path: node for node in graph.nodes if node.kind == "file"
    }
    imports = _file_import_edges(graph)
    out_degree: Counter[str] = Counter()
    in_degree: Counter[str] = Counter()
    package_edges: Counter[tuple[str, str]] = Counter()
    layer_edges: Counter[tuple[str, str]] = Counter()
    adjacency: dict[str, set[str]] = defaultdict(set)

    for src_path, dst_path in imports:
        out_degree[src_path] += 1
        in_degree[dst_path] += 1
        adjacency[src_path].add(dst_path)
        src, dst = path_index.get(src_path), path_index.get(dst_path)
        if not src or not dst:
            continue
        if src.package != dst.package:
            package_edges[(src.package, dst.package)] += 1
        if src.layer != dst.layer:
            layer_edges[(src.layer, dst.layer)] += 1

    layer_files: Counter[str] = Counter()
    layer_loc: Counter[str] = Counter()
    layer_symbols: Counter[str] = Counter()
    for node in graph.nodes:
        if node.kind == "file":
            layer_files[node.layer] += 1
            layer_loc[node.layer] += node.loc
        else:
            layer_symbols[node.layer] += 1

    call_in: Counter[str] = Counter()
    call_out: Counter[str] = Counter()
    for edge in graph.edges:
        if edge.kind == "calls":
            call_in[edge.dst] += 1
            call_out[edge.src] += 1

    cycles = _tarjan(list(adjacency), adjacency)
    cycle_details = []
    for component in cycles:
        members = sorted(component)
        internal = [
            (src, dst)
            for src, dst in imports
            if src in component and dst in component
        ]
        cycle_details.append({"members": members, "internal_edges": internal})

    # 没有任何被导入边的文件（测试入口、脚本、迁移工具等会大量出现）。
    all_files = {node.path for node in graph.nodes if node.kind == "file"}
    orphan_files = sorted(path for path in all_files if in_degree[path] == 0)

    god_files = sorted(
        (
            {
                "path": path,
                "in": in_degree[path],
                "out": out_degree[path],
                "total": in_degree[path] + out_degree[path],
            }
            for path in all_files
        ),
        key=lambda item: (-item["total"], item["path"]),
    )[:30]

    god_symbols = sorted(
        (
            {
                "id": symbol,
                "file": index[symbol].path if symbol in index else "",
                "line": index[symbol].line if symbol in index else 0,
                "in": call_in[symbol],
                "out": call_out[symbol],
            }
            for symbol in set(call_in) | set(call_out)
            if symbol in index
        ),
        key=lambda item: (-item["in"], item["id"]),
    )[:30]

    unresolved_by_source: Counter[str] = Counter(
        str(item.get("source", "")) for item in graph.unresolved_imports
    )

    return {
        "summary": {
            "files": sum(1 for node in graph.nodes if node.kind == "file"),
            "symbols": sum(1 for node in graph.nodes if node.kind != "file"),
            "edges": len(graph.edges),
            "import_edges": len(imports),
            "call_edges": sum(1 for edge in graph.edges if edge.kind == "calls"),
            "inherit_edges": sum(
                1 for edge in graph.edges if edge.kind == "inherits"
            ),
            "layers": len({node.layer for node in graph.nodes}),
            "cycles": len(cycles),
            "unresolved_imports": len(graph.unresolved_imports),
            "orphan_files": len(orphan_files),
        },
        "layers": [
            {
                "layer": layer,
                "title": layer_title(layer),
                "files": layer_files[layer],
                "loc": layer_loc[layer],
                "symbols": layer_symbols[layer],
            }
            for layer in sorted(
                set(layer_files) | set(layer_symbols),
                key=lambda item: (-layer_loc[item], item),
            )
        ],
        "layer_edges": [
            {"src": src, "dst": dst, "count": count}
            for (src, dst), count in layer_edges.most_common()
        ],
        "package_edges": [
            {"src": src, "dst": dst, "count": count}
            for (src, dst), count in package_edges.most_common()
        ],
        "god_files": god_files,
        "god_symbols": god_symbols,
        "cycles": cycle_details,
        "orphan_files": orphan_files,
        "unresolved_by_source": unresolved_by_source.most_common(),
    }


def _mermaid_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def render_layer_graph(
    metrics: dict[str, Any], *, exclude: set[str] | None = None
) -> str:
    titles = all_layer_titles()
    exclude = exclude or set()
    active = {
        item["layer"] for item in metrics["layers"] if item["layer"] not in exclude
    }
    lines = ["flowchart LR"]
    for layer in sorted(active):
        lines.append(
            f'    {_mermaid_id(layer)}["{titles.get(layer, layer)}"]'
        )
    for edge in metrics["layer_edges"]:
        if edge["src"] in active and edge["dst"] in active:
            lines.append(
                f'    {_mermaid_id(edge["src"])} -->|{edge["count"]}| '
                f'{_mermaid_id(edge["dst"])}'
            )
    return "\n".join(lines)


def render_package_graph(
    metrics: dict[str, Any],
    min_count: int = 2,
    *,
    exclude_prefixes: tuple[str, ...] = (),
) -> str:
    lines = ["flowchart LR"]
    edges = [
        edge
        for edge in metrics["package_edges"]
        if edge["count"] >= min_count
        and not edge["src"].startswith(exclude_prefixes)
        and not edge["dst"].startswith(exclude_prefixes)
    ]
    allowed = {edge["src"] for edge in edges} | {edge["dst"] for edge in edges}
    for package in sorted(allowed):
        lines.append(f'    {_mermaid_id(package)}["{package}"]')
    for edge in edges:
        lines.append(
            f'    {_mermaid_id(edge["src"])} -->|{edge["count"]}| '
            f'{_mermaid_id(edge["dst"])}'
        )
    return "\n".join(lines)


def render_metrics_markdown(metrics: dict[str, Any], generated_at: str) -> str:
    summary = metrics["summary"]
    lines = [
        "# 结构与依赖指标",
        "",
        f"> 生成时间：{generated_at}",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 文件 | {summary['files']} |",
        f"| 符号 | {summary['symbols']} |",
        f"| 边 | {summary['edges']} |",
        f"| import 边 | {summary['import_edges']} |",
        f"| call 边 | {summary['call_edges']} |",
        f"| inherit 边 | {summary['inherit_edges']} |",
        f"| 分层数 | {summary['layers']} |",
        f"| 文件级循环 | {summary['cycles']} |",
        f"| 未解析导入 | {summary['unresolved_imports']} |",
        f"| 无入边文件 | {summary['orphan_files']} |",
        "",
        "## 分层规模",
        "",
        "| 分层 | 文件 | LOC | 符号 |",
        "|---|---:|---:|---:|",
    ]
    for item in metrics["layers"]:
        lines.append(
            f"| {item['title']} (`{item['layer']}`) | {item['files']} | "
            f"{item['loc']} | {item['symbols']} |"
        )
    lines.extend(
        [
            "",
            "## 分层依赖（跨层 import 计数）",
            "",
            "| 从 | 到 | 次数 |",
            "|---|---|---:|",
        ]
    )
    for edge in metrics["layer_edges"]:
        lines.append(
            f"| `{edge['src']}` | `{edge['dst']}` | {edge['count']} |"
        )
    lines.extend(
        [
            "",
            "## 高连接文件（import 入度 + 出度）",
            "",
            "| 文件 | 入度 | 出度 | 合计 |",
            "|---|---:|---:|---:|",
        ]
    )
    for item in metrics["god_files"]:
        lines.append(
            f"| `{item['path']}` | {item['in']} | {item['out']} | {item['total']} |"
        )
    lines.extend(
        [
            "",
            "## 被调用最多的符号（call 入度）",
            "",
            "| 符号 | 位置 | 入度 | 出度 |",
            "|---|---|---:|---:|",
        ]
    )
    for item in metrics["god_symbols"]:
        location = f"{item['file']}:{item['line']}" if item["file"] else ""
        lines.append(
            f"| `{item['id']}` | `{location}` | {item['in']} | {item['out']} |"
        )
    return "\n".join(lines) + "\n"


def render_cycles_markdown(metrics: dict[str, Any]) -> str:
    lines = ["# 文件级循环依赖", ""]
    if not metrics["cycles"]:
        lines.append("未发现文件级 import 循环。")
        return "\n".join(lines) + "\n"
    for number, cycle in enumerate(metrics["cycles"], start=1):
        lines.append(f"## 循环 {number}（{len(cycle['members'])} 个文件）")
        lines.append("")
        for member in cycle["members"]:
            lines.append(f"- `{member}`")
        lines.append("")
        lines.append("内部边：")
        lines.append("")
        for src, dst in cycle["internal_edges"]:
            lines.append(f"- `{src}` → `{dst}`")
        lines.append("")
    return "\n".join(lines)


def write_reports(
    graph: Graph, out_dir: Path, *, root: str, generated_at: str | None = None
) -> dict[str, Any]:
    """写出 graph.json 与全部 Markdown/Mermaid 报告，返回指标。"""

    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )
    metrics = build_metrics(graph)
    payload = graph.to_dict(root=root, generated_at=generated_at)
    (out_dir / "graph.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "metrics.md").write_text(
        render_metrics_markdown(metrics, generated_at), encoding="utf-8"
    )
    (out_dir / "layer-graph.mmd").write_text(
        render_layer_graph(metrics), encoding="utf-8"
    )
    (out_dir / "layer-graph-core.mmd").write_text(
        render_layer_graph(metrics, exclude={"tests", "tooling", "docs"}),
        encoding="utf-8",
    )
    (out_dir / "package-graph.mmd").write_text(
        render_package_graph(metrics), encoding="utf-8"
    )
    (out_dir / "package-graph-core.mmd").write_text(
        render_package_graph(
            metrics, min_count=1, exclude_prefixes=("tests.", "tmp_")
        ),
        encoding="utf-8",
    )
    subsystems_payload = semantic_graph()
    (out_dir / "subsystems.json").write_text(
        json.dumps(subsystems_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "subsystem-graph.mmd").write_text(
        render_semantic_mermaid(), encoding="utf-8"
    )
    (out_dir / "cycles.md").write_text(
        render_cycles_markdown(metrics), encoding="utf-8"
    )
    unresolved_lines = [
        "# 未解析导入",
        "",
        "这些导入指向仓库内命名空间但未能解析到具体文件，通常意味着模块已删除或路径写错。",
        "",
        "| 源文件 | 目标 | 行 |",
        "|---|---|---:|",
    ]
    for item in graph.unresolved_imports:
        unresolved_lines.append(
            f"| `{item.get('source', '')}` | `{item.get('target', '')}` | "
            f"{item.get('line', 0)} |"
        )
    (out_dir / "unresolved.md").write_text(
        "\n".join(unresolved_lines) + "\n", encoding="utf-8"
    )
    write_visualization(
        graph_payload=payload,
        metrics=metrics,
        subsystems=subsystems_payload,
        out_path=out_dir / "graph.html",
    )
    return metrics
