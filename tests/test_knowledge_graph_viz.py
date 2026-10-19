"""知识图谱 HTML 可视化的载荷裁剪与离线约束测试。"""

from __future__ import annotations

import json

from scripts.knowledge_graph.model import Edge, Graph, Node
from scripts.knowledge_graph.report import write_reports
from scripts.knowledge_graph.viz import build_viz_payload, render_html


def _graph_payload() -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-10-19T00:00:00+08:00",
        "root": ".",
        "stats": {"files": 1, "symbols": 2},
        "nodes": [
            {
                "id": "file:a.py", "kind": "file", "name": "a.py",
                "qualified_name": "a", "path": "a.py", "language": "python",
                "line": 0, "end_line": 0, "package": "a", "layer": "core", "loc": 10,
            },
            {
                "id": "func:a.py:run", "kind": "function", "name": "run",
                "qualified_name": "a.run", "path": "a.py", "language": "python",
                "line": 3, "end_line": 4, "package": "a", "layer": "core", "loc": 2,
            },
        ],
        "edges": [
            {"src": "file:a.py", "dst": "func:a.py:run", "kind": "defines"},
            {"src": "file:b.py", "dst": "file:a.py", "kind": "imports", "line": 1},
            {"src": "func:a.py:run", "dst": "func:a.py:run", "kind": "calls"},
        ],
        "unresolved_imports": [],
        "external_modules": {},
    }


def _metrics() -> dict:
    return {
        "summary": {"files": 1, "symbols": 1},
        "layers": [{"layer": "core", "title": "核心", "files": 1,
                    "loc": 10, "symbols": 1}],
        "layer_edges": [],
        "god_files": [],
        "cycles": [],
    }


def _subsystems() -> dict:
    return {
        "nodes": [{"id": "sdk", "title": "SDK", "layers": ["core"],
                   "paths": ["a.py"], "responsibility": "入口"}],
        "edges": [],
    }


def test_payload_drops_defines_edges_and_internal_fields() -> None:
    payload = build_viz_payload(_graph_payload(), _metrics(), _subsystems())

    assert [edge["kind"] for edge in payload["edges"]] == ["imports", "calls"]
    node = payload["nodes"][1]
    assert set(node) == {"id", "kind", "name", "path", "layer", "line", "loc"}
    assert payload["metrics"]["summary"]["files"] == 1
    assert payload["subsystems"]["nodes"][0]["id"] == "sdk"


def test_html_embeds_payload_and_has_no_external_assets() -> None:
    html = render_html(build_viz_payload(_graph_payload(), _metrics(), _subsystems()))

    assert '<script type="application/json" id="graph-data">' in html
    assert "file:a.py" in html
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert "cdn" not in html.lower()


def test_script_end_tag_in_data_is_escaped() -> None:
    graph = _graph_payload()
    graph["nodes"][0]["name"] = "</script><script>alert(1)</script>"

    html = render_html(build_viz_payload(graph, _metrics(), _subsystems()))

    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script>" in html


def test_write_reports_emits_visualization(tmp_path) -> None:
    graph = Graph()
    graph.add_nodes([
        Node(id="file:a.py", kind="file", name="a.py", qualified_name="a",
             path="a.py", language="python", layer="core", loc=10),
        Node(id="func:a.py:run", kind="function", name="run",
             qualified_name="a.run", path="a.py", language="python",
             line=3, end_line=4, layer="core", loc=2),
    ])
    graph.add_edges([
        Edge(src="file:a.py", dst="func:a.py:run", kind="defines"),
        Edge(src="file:b.py", dst="file:a.py", kind="imports", line=1),
    ])

    write_reports(graph, tmp_path, root=".", generated_at="2026-10-19T00:00:00+08:00")

    html = (tmp_path / "graph.html").read_text(encoding="utf-8")
    assert "graph-data" in html
    assert "FloodMind 知识图谱" in html
    # 内嵌载荷本身仍可被解析，且只保留可视化字段。
    raw = html.split('<script type="application/json" id="graph-data">', 1)[1]
    raw = raw.split("</script>", 1)[0]
    payload = json.loads(raw)
    assert payload["nodes"][0]["id"] == "file:a.py"
