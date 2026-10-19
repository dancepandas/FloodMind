"""知识图谱的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Node:
    """图中的一个节点：文件、类、函数或方法。"""

    id: str
    kind: str
    name: str
    qualified_name: str
    path: str
    language: str
    line: int = 0
    end_line: int = 0
    package: str = ""
    layer: str = ""
    loc: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "path": self.path,
            "language": self.language,
            "line": self.line,
            "end_line": self.end_line,
            "package": self.package,
            "layer": self.layer,
            "loc": self.loc,
        }


@dataclass(frozen=True)
class Edge:
    """有向边。kind: imports / calls / inherits / defines。"""

    src: str
    dst: str
    kind: str
    line: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind,
        }
        if self.line:
            data["line"] = self.line
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class Graph:
    """节点与边的集合，附带生成元数据。"""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved_imports: list[dict[str, Any]] = field(default_factory=list)
    external_modules: dict[str, int] = field(default_factory=dict)
    language_files: dict[str, int] = field(default_factory=dict)

    def add_nodes(self, nodes: Iterable[Node]) -> None:
        self.nodes.extend(nodes)

    def add_edges(self, edges: Iterable[Edge]) -> None:
        self.edges.extend(edges)

    def to_dict(self, *, root: str, generated_at: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "generated_at": generated_at,
            "root": root,
            "stats": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "files": sum(1 for n in self.nodes if n.kind == "file"),
                "symbols": sum(1 for n in self.nodes if n.kind != "file"),
                "languages": dict(sorted(self.language_files.items())),
            },
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "unresolved_imports": self.unresolved_imports,
            "external_modules": dict(
                sorted(self.external_modules.items(), key=lambda item: -item[1])
            ),
        }
