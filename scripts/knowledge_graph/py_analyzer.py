"""Python 源码静态分析：文件/符号/导入/调用/继承。

解析是保守的：只记录能解析到仓库内目标的边，未解析项单独统计，
避免把猜测写进依赖图。
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .layers import layer_for_path
from .model import Edge, Graph, Node

_SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    ".git",
    ".pytest_cache",
    ".idea",
    ".repopedia",
    ".floodmind",
    ".claude",
    ".superpowers",
    ".opencode",
}
# 仅跳过仓库根目录下的产物/运行目录/独立子项目；不要误伤 floodmind/agent/runtime。
_TOP_LEVEL_SKIP = {
    "node_modules",
    "dist",
    "build",
    "logs",
    "conversations",
    "artifacts",
    "runtime",
    "sbx",
    "data",
    "floodmind.egg-info",
    # 独立项目/实验目录，不属于 FloodMind Python 包边界。
    "deepseek-harness",
    "desktop",
    "tools",
    "contrib",
}

_SKIP_FILE_PREFIXES = ("tmp_",)


@dataclass
class DefInfo:
    id: str
    kind: str
    name: str
    qualname: str
    module: str
    path: str
    line: int
    end_line: int
    parent_class: str | None
    node: ast.AST = field(repr=False, default=None)


@dataclass
class FileInfo:
    path: str
    module: str
    package: str
    loc: int
    tree: ast.Module


def iter_python_files(root: Path) -> Iterator[str]:
    """遍历仓库内 Python 文件，返回相对 posix 路径。"""

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
            if filename.endswith(".py") and not filename.startswith(
                _SKIP_FILE_PREFIXES
            ):
                yield (rel_dir / filename).as_posix()


def module_name_for_path(path: str) -> str:
    parts = path[:-3].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_for_module(module: str, depth: int = 3) -> str:
    parts = module.split(".")
    return ".".join(parts[:depth]) if len(parts) >= depth else module


def _resolve_relative(
    module: str, is_package: bool, level: int, name: str
) -> str:
    if not level:
        return name
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    if name:
        base.extend(name.split("."))
    return ".".join(part for part in base if part)


def _call_detail(node: ast.AST) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - 极端语法
        return ""
    text = " ".join(text.split())
    return text[:120]


def _flatten_attribute(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return list(reversed(parts))
    return None


def _iter_scope_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """遍历一个作用域内的节点，但不进入嵌套函数/类。"""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _iter_scope_nodes(child)


def _collect_defs(
    body: Iterable[ast.stmt],
    module: str,
    path: str,
    prefix: str = "",
    parent_class: str | None = None,
) -> list[DefInfo]:
    defs: list[DefInfo] = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{stmt.name}"
            kind = "method" if parent_class else "function"
            defs.append(
                DefInfo(
                    id=f"symbol:{module}.{qualname}",
                    kind=kind,
                    name=stmt.name,
                    qualname=qualname,
                    module=module,
                    path=path,
                    line=stmt.lineno,
                    end_line=getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno,
                    parent_class=parent_class,
                    node=stmt,
                )
            )
            defs.extend(
                _collect_defs(stmt.body, module, path, f"{qualname}.", None)
            )
        elif isinstance(stmt, ast.ClassDef):
            qualname = f"{prefix}{stmt.name}"
            defs.append(
                DefInfo(
                    id=f"symbol:{module}.{qualname}",
                    kind="class",
                    name=stmt.name,
                    qualname=qualname,
                    module=module,
                    path=path,
                    line=stmt.lineno,
                    end_line=getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno,
                    parent_class=parent_class,
                    node=stmt,
                )
            )
            defs.extend(
                _collect_defs(stmt.body, module, path, f"{qualname}.", qualname)
            )
        elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            # 条件导入/条件定义：继续向下找定义。
            for attr in ("body", "orelse", "finalbody", "handlers"):
                block = getattr(stmt, attr, None)
                if isinstance(block, list):
                    defs.extend(
                        _collect_defs(block, module, path, prefix, parent_class)
                    )
    return defs


class _CallResolver:
    def __init__(
        self,
        module: str,
        module_symbols: dict[str, str],
        aliases: dict[str, str],
        class_methods: dict[str, dict[str, str]],
        symbol_kinds: dict[str, str],
    ) -> None:
        self.module = module
        self.module_symbols = module_symbols
        self.aliases = aliases
        self.class_methods = class_methods
        self.symbol_kinds = symbol_kinds

    def resolve(
        self, func: ast.AST, parent_class: str | None
    ) -> str | None:
        if isinstance(func, ast.Name):
            return self._resolve_name(func.id, parent_class)
        if isinstance(func, ast.Attribute):
            return self._resolve_attribute(func, parent_class)
        return None

    def _resolve_name(self, name: str, parent_class: str | None) -> str | None:
        if parent_class:
            methods = self.class_methods.get(parent_class, {})
            if name in methods:
                return methods[name]
        if name in self.module_symbols:
            return self.module_symbols[name]
        target = self.aliases.get(name)
        if target and target.startswith("symbol:"):
            return target
        return None

    def _resolve_attribute(
        self, node: ast.Attribute, parent_class: str | None
    ) -> str | None:
        parts = _flatten_attribute(node)
        if not parts or len(parts) < 2:
            return None
        base, rest = parts[0], parts[1:]
        if base in {"self", "cls"} and parent_class:
            methods = self.class_methods.get(parent_class, {})
            return methods.get(rest[0])
        alias = self.aliases.get(base)
        if alias and alias.startswith("module:"):
            module = alias[len("module:") :]
            candidate = f"symbol:{module}.{'.'.join(rest)}"
            if candidate in self.symbol_kinds:
                return candidate
            return None
        base_symbol = self.module_symbols.get(base)
        if base_symbol:
            candidate = f"{base_symbol}.{'.'.join(rest)}"
            if candidate in self.symbol_kinds:
                return candidate
        return None


def analyze_python(root: Path, graph: Graph) -> None:
    """解析 Python 源码并向 graph 追加节点与边。"""

    files: dict[str, FileInfo] = {}
    module_to_file: dict[str, str] = {}
    for rel in iter_python_files(root):
        path = root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:  # pragma: no cover - 语法错误单独可见
            graph.unresolved_imports.append(
                {"source": rel, "target": f"<parse-error:{exc.msg}>", "line": exc.lineno or 0}
            )
            continue
        module = module_name_for_path(rel)
        info = FileInfo(
            path=rel,
            module=module,
            package=package_for_module(module),
            loc=source.count("\n") + 1,
            tree=tree,
        )
        files[rel] = info
        module_to_file[module] = rel

    local_tops = {module.split(".")[0] for module in module_to_file}
    unresolved: dict[tuple[str, str], dict[str, object]] = {}
    external: dict[str, int] = {}
    nodes: list[Node] = list(graph.nodes)
    edges: list[Edge] = list(graph.edges)

    def resolve_import_target(target: str) -> str | None:
        if target in module_to_file:
            return target
        # from a.b import c 可能指向子模块；调用方已尝试拼接，
        # 这里再容忍父包存在但目标模块缺失的情况。
        return None

    for rel, info in files.items():
        module = info.module
        is_package = rel.endswith("__init__.py")
        aliases: dict[str, str] = {}
        imports: dict[str, tuple[int, str]] = {}

        for node in ast.walk(info.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    resolved = resolve_import_target(target)
                    if resolved:
                        imports.setdefault(resolved, (node.lineno, target))
                    top = target.split(".")[0]
                    if resolved:
                        if alias.asname:
                            aliases[alias.asname] = f"module:{resolved}"
                        else:
                            aliases[top] = f"module:{top}"
                    elif top in local_tops:
                        unresolved.setdefault(
                            (rel, target), {"source": rel, "target": target, "line": node.lineno}
                        )
                    else:
                        external[top] = external.get(top, 0) + 1
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_relative(module, is_package, node.level, node.module or "")
                if not base:
                    continue
                resolved_base = resolve_import_target(base)
                if resolved_base:
                    imports.setdefault(resolved_base, (node.lineno, base))
                elif base.split(".")[0] in local_tops:
                    unresolved.setdefault(
                        (rel, base), {"source": rel, "target": base, "line": node.lineno}
                    )
                elif base.split(".")[0] not in {"", "."}:
                    external[base.split(".")[0]] = external.get(base.split(".")[0], 0) + 1
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    sub = f"{base}.{alias.name}"
                    resolved_sub = resolve_import_target(sub)
                    key = alias.asname or alias.name
                    if resolved_sub:
                        imports.setdefault(resolved_sub, (node.lineno, sub))
                        aliases[key] = f"module:{resolved_sub}"
                    else:
                        aliases[key] = f"symbol:{sub}"

        package = info.package
        layer = layer_for_path(rel)
        file_id = f"file:{rel}"
        nodes.append(
            Node(
                id=file_id,
                kind="file",
                name=Path(rel).name,
                qualified_name=module,
                path=rel,
                language="python",
                package=package,
                layer=layer,
                loc=info.loc,
            )
        )
        for target, (line, raw) in imports.items():
            edges.append(
                Edge(
                    src=file_id,
                    dst=f"file:{module_to_file[target]}",
                    kind="imports",
                    line=line,
                    detail=raw,
                )
            )
        info_aliases = aliases  # 保存给第二阶段使用
        files[rel].tree = info.tree  # type: ignore[misc]
        setattr(info, "_aliases", info_aliases)

    # 第二阶段：符号、定义边、调用边、继承边。
    defs_by_file: dict[str, list[DefInfo]] = {}
    symbol_kinds: dict[str, str] = {}
    module_symbols: dict[str, dict[str, str]] = {}
    class_methods: dict[str, dict[str, str]] = {}

    for rel, info in files.items():
        defs = _collect_defs(info.tree.body, info.module, rel)
        defs_by_file[rel] = defs
        module_symbols[info.module] = {}
        for definition in defs:
            symbol_kinds[definition.id] = definition.kind
            nodes.append(
                Node(
                    id=definition.id,
                    kind=definition.kind,
                    name=definition.name,
                    qualified_name=definition.qualname,
                    path=rel,
                    language="python",
                    line=definition.line,
                    end_line=definition.end_line,
                    package=info.package,
                    layer=layer_for_path(rel),
                )
            )
            if "." not in definition.qualname:
                module_symbols[info.module][definition.name] = definition.id
            if definition.kind == "method" and definition.parent_class:
                class_methods.setdefault(definition.parent_class, {})[
                    definition.name
                ] = definition.id
        for definition in defs:
            edges.append(
                Edge(
                    src=f"file:{rel}",
                    dst=definition.id,
                    kind="defines",
                    line=definition.line,
                )
            )

    # 导入别名依赖符号表，补一轮：把 from module import 名称解析成符号。
    for rel, info in files.items():
        aliases: dict[str, str] = getattr(info, "_aliases", {})
        for key, target in list(aliases.items()):
            if target.startswith("symbol:"):
                symbol = target[len("symbol:") :]
                if symbol in symbol_kinds:
                    aliases[key] = target
                else:
                    aliases.pop(key)

    call_sites: dict[tuple[str, str], tuple[int, int, str]] = {}
    inherit_edges: set[tuple[str, str, int]] = set()

    for rel, info in files.items():
        aliases: dict[str, str] = getattr(info, "_aliases", {})
        resolver = _CallResolver(
            module=info.module,
            module_symbols=module_symbols.get(info.module, {}),
            aliases=aliases,
            class_methods=class_methods,
            symbol_kinds=symbol_kinds,
        )
        # 模块级调用归属到文件节点。
        for node in _iter_scope_nodes(info.tree):
            if isinstance(node, ast.Call):
                target = resolver.resolve(node.func, None)
                if target:
                    key = (f"file:{rel}", target)
                    count, first, detail = call_sites.get(key, (0, node.lineno, ""))
                    call_sites[key] = (
                        count + 1,
                        first or node.lineno,
                        detail or _call_detail(node.func),
                    )
        for definition in defs_by_file[rel]:
            if definition.kind in {"function", "method"}:
                for node in _iter_scope_nodes(definition.node):
                    if isinstance(node, ast.Call):
                        target = resolver.resolve(
                            node.func, definition.parent_class
                        )
                        if target and target != definition.id:
                            key = (definition.id, target)
                            count, first, detail = call_sites.get(
                                key, (0, node.lineno, "")
                            )
                            call_sites[key] = (
                                count + 1,
                                first or node.lineno,
                                detail or _call_detail(node.func),
                            )
            elif definition.kind == "class":
                for base in getattr(definition.node, "bases", []):
                    target = None
                    if isinstance(base, ast.Name):
                        target = resolver._resolve_name(base.id, None)
                    elif isinstance(base, ast.Attribute):
                        target = resolver._resolve_attribute(base, None)
                    if target and symbol_kinds.get(target) == "class":
                        inherit_edges.add((definition.id, target, definition.line))

    for (src, dst), (count, line, detail) in sorted(call_sites.items()):
        edges.append(
            Edge(
                src=src,
                dst=dst,
                kind="calls",
                line=line,
                detail=f"{detail} (x{count})" if count > 1 else detail,
            )
        )
    for src, dst, line in sorted(inherit_edges):
        edges.append(Edge(src=src, dst=dst, kind="inherits", line=line))

    graph.nodes = nodes
    graph.edges = edges
    graph.unresolved_imports.extend(unresolved.values())
    for name, count in external.items():
        graph.external_modules[name] = graph.external_modules.get(name, 0) + count
    graph.language_files["python"] = len(files)
