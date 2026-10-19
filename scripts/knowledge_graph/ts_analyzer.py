"""TypeScript/JavaScript 源码的轻量导入分析。

前端只关心模块边界与依赖方向，因此这里用正则提取 import/require/export，
不做完整语法树解析；无法解析到仓库内文件的导入计入 unresolved。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

from .layers import layer_for_path
from .model import Edge, Graph, Node

_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
_SKIP_DIR_NAMES = {
    "node_modules",
    "dist",
    "build",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "vendor",
    "coverage",
}
_TOP_LEVEL_SKIP = {
    "node_modules",
    "dist",
    "build",
    # 独立项目/实验目录，不属于 FloodMind 前端边界。
    "deepseek-harness",
    "desktop",
    "tools",
    "contrib",
}

_IMPORT_PATTERNS = (
    re.compile(r"""import\s+(?:type\s+)?[\s\S]*?\s+from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""import\s+['"]([^'"]+)['"]"""),
    re.compile(r"""export\s+[\s\S]*?\s+from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)"""),
)

_ALIAS_ROOTS = (
    ("desktop/", "desktop/src/"),
)


def iter_source_files(root: Path) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        parts = rel_dir.parts
        if parts and parts[0] in _TOP_LEVEL_SKIP:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIR_NAMES and not d.startswith(".")
        )
        for filename in sorted(filenames):
            if filename.endswith(_TS_EXTENSIONS):
                yield (rel_dir / filename).as_posix()


def _resolve_relative(
    source: str, spec: str, all_files: set[str]
) -> str | None:
    base = Path(source).parent
    candidate = Path(os.path.normpath((base / spec).as_posix())).as_posix()
    if candidate.startswith("../") or candidate == "..":
        return None
    candidates = [candidate]
    for ext in _TS_EXTENSIONS:
        candidates.append(candidate + ext)
    for ext in _TS_EXTENSIONS:
        candidates.append(f"{candidate}/index{ext}")
    for item in candidates:
        if item in all_files:
            return item
    return None


def _resolve_alias(source: str, spec: str, all_files: set[str]) -> str | None:
    for prefix, src_root in _ALIAS_ROOTS:
        if not source.startswith(prefix):
            continue
        if spec.startswith("@/"):
            candidate = f"{src_root}{spec[2:]}"
        elif spec.startswith("~/"):
            candidate = f"{prefix}{spec[2:]}"
        else:
            return None
        candidates = [candidate]
        for ext in _TS_EXTENSIONS:
            candidates.append(candidate + ext)
        for ext in _TS_EXTENSIONS:
            candidates.append(f"{candidate}/index{ext}")
        for item in candidates:
            if item in all_files:
                return item
    return None


def analyze_typescript(root: Path, graph: Graph) -> None:
    files: set[str] = set(iter_source_files(root))
    if not files:
        return

    nodes: list[Node] = list(graph.nodes)
    edges: list[Edge] = list(graph.edges)
    unresolved: list[dict[str, object]] = []

    for rel in sorted(files):
        path = root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8", errors="replace")
        nodes.append(
            Node(
                id=f"file:{rel}",
                kind="file",
                name=Path(rel).name,
                qualified_name=rel,
                path=rel,
                language=_language_for(rel),
                package=_package_for(rel),
                layer=layer_for_path(rel),
                loc=source.count("\n") + 1,
            )
        )

        seen: set[str] = set()
        for pattern in _IMPORT_PATTERNS:
            for match in pattern.finditer(source):
                spec = match.group(1).strip()
                line = source.count("\n", 0, match.start()) + 1
                target = None
                if spec.startswith("."):
                    target = _resolve_relative(rel, spec, files)
                elif spec.startswith(("@/", "~/")):
                    target = _resolve_alias(rel, spec, files)
                if target is None:
                    if spec.startswith("."):
                        unresolved.append(
                            {
                                "source": rel,
                                "target": spec,
                                "line": line,
                                "language": "typescript",
                            }
                        )
                    continue
                if target in seen:
                    continue
                seen.add(target)
                edges.append(
                    Edge(
                        src=f"file:{rel}",
                        dst=f"file:{target}",
                        kind="imports",
                        line=line,
                        detail=spec,
                    )
                )

    graph.nodes = nodes
    graph.edges = edges
    graph.unresolved_imports.extend(unresolved)
    graph.language_files["typescript"] = sum(
        1 for name in files if name.endswith((".ts", ".tsx"))
    )
    graph.language_files["javascript"] = sum(
        1 for name in files if name.endswith((".js", ".jsx", ".mjs", ".cjs"))
    )
    graph.language_files["frontend-other"] = sum(
        1 for name in files if name.endswith((".vue", ".svelte"))
    )


def _language_for(path: str) -> str:
    if path.endswith((".ts", ".tsx")):
        return "typescript"
    if path.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
    return "frontend"


def _package_for(path: str) -> str:
    parts = path.split("/")
    if len(parts) <= 2:
        return path
    if parts[0] == "web" and parts[1] == "src":
        return ".".join(parts[:3])
    if parts[0] == "floodmind" and len(parts) >= 4:
        return ".".join(parts[:4])
    return ".".join(parts[:2])
