"""Ground truth: what the source actually says.

Everything this module emits carries `Provenance(kind="ast")` with a real
`path:line`. Nothing here guesses. If a call site cannot be resolved to an
internal module, it is **counted as unresolved and no edge is drawn** — we do
not invent a plausible target.

That choice is the whole point. A static crawler over Python will always miss
edges: `getattr`, dynamic dispatch, registries, string-keyed factories. The
honest response is to make the miss visible (`Coverage.unresolved_calls`) rather
than to paper it with a likely-looking arrow. An absence in this graph means
"static analysis did not find it", never "it is not there" — and the UI is
required to say so.
"""

from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
from typing import Iterable, Optional

_BUILTINS = frozenset(dir(builtins))

from ..model import CodeGraph, Coverage, Edge, Node, Provenance

# Directories that are never part of the architecture being mapped.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "env", "node_modules", ".tox", ".eggs",
    "build", "dist", "site-packages", ".idea", ".vscode", ".claude",
}


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def iter_python_files(root: Path, skip_dirs: Optional[set[str]] = None) -> Iterable[Path]:
    """Yield every .py file under root, skipping vendored and generated trees."""
    skip = skip_dirs if skip_dirs is not None else SKIP_DIRS
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not (d in skip or d.endswith(".egg-info"))]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def module_name_for(path: Path, root: Path) -> str:
    """Dotted module name for a file, relative to the crawl root.

    `core/pnl.py` -> `core.pnl`;  `core/__init__.py` -> `core`.
    """
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else rel.stem


class _ModuleVisitor(ast.NodeVisitor):
    """Collects definitions, imports and call sites for one module.

    Import bindings are tracked so a bare call like `realized_pnl(...)` can be
    attributed to the module it was imported from. Bindings established inside a
    function body (this codebase's lazy-import idiom, `from core import x as _ol`)
    are recorded the same way — they are ordinary AST nodes and there is no reason
    to miss them.
    """

    def __init__(self, module: str, rel_path: str, internal: set[str]):
        self.module = module
        self.rel_path = rel_path
        self.internal = internal

        # name in this module's namespace -> internal module it came from
        self.bindings: dict[str, str] = {}
        # local alias -> internal module, for `import x.y as z` / `import x.y`
        self.module_aliases: dict[str, str] = {}

        # names bound from OUTSIDE the crawled tree (stdlib, third party)
        self.external_names: set[str] = set()
        # every function/class name defined in this module; pre-populated by the
        # caller because a call can appear textually before its definition.
        self.local_defs: set[str] = set()

        self.defs: list[tuple[str, str, int, int]] = []   # (qualname, kind, lineno, end_lineno)
        self.import_edges: list[tuple[str, int]] = []      # (target_module, lineno)
        self.calls: list[tuple[Optional[str], Optional[str], str, int, str]] = []
        # (target_module | None, target_symbol | None, raw_text, lineno, category)
        # category is one of: internal | builtin | local_method | external | unresolved

        self._scope: list[str] = []

    # -- imports -------------------------------------------------------------

    def _resolve_internal(self, dotted: str) -> Optional[str]:
        """Longest internal module prefix of a dotted name, or None."""
        if dotted in self.internal:
            return dotted
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.internal:
                return cand
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = self._resolve_internal(alias.name)
            if not target:
                self.external_names.add(alias.asname or alias.name.split(".")[0])
            if target:
                self.import_edges.append((target, node.lineno))
                local = alias.asname or alias.name.split(".")[0]
                # `import a.b.c` binds `a`; only a full alias binds the leaf.
                self.module_aliases[local] = target if alias.asname else (
                    self._resolve_internal(alias.name.split(".")[0]) or target
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._module_from_importfrom(node)
        if base is None:
            for alias in node.names:
                self.external_names.add(alias.asname or alias.name)
            self.generic_visit(node)
            return

        for alias in node.names:
            if alias.name == "*":
                self.import_edges.append((base, node.lineno))
                continue
            # `from pkg import mod` where pkg.mod is itself a module
            submodule = f"{base}.{alias.name}"
            target = submodule if submodule in self.internal else base
            self.import_edges.append((target, node.lineno))
            local = alias.asname or alias.name
            if submodule in self.internal:
                self.module_aliases[local] = submodule
            else:
                self.bindings[local] = base
        self.generic_visit(node)

    def _module_from_importfrom(self, node: ast.ImportFrom) -> Optional[str]:
        """Resolve `from .x import y` and `from a.b import y` to an internal module."""
        if node.level:
            pkg_parts = self.module.split(".")
            # Inside a package `__init__`, level 1 means the package itself.
            up = node.level - 1
            base_parts = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts[:]
            # A module (not a package) resolves relative to its parent package.
            if base_parts and base_parts == pkg_parts:
                base_parts = pkg_parts[:-1]
            if node.module:
                base_parts = base_parts + node.module.split(".")
            dotted = ".".join(p for p in base_parts if p)
            return self._resolve_internal(dotted) or (dotted if dotted in self.internal else None)
        if not node.module:
            return None
        return self._resolve_internal(node.module)

    # -- definitions ---------------------------------------------------------

    def _record_def(self, node, kind: str) -> None:
        qual = ".".join(self._scope + [node.name])
        self.defs.append((qual, kind, node.lineno, getattr(node, "end_lineno", node.lineno)))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_def(node, "method" if self._scope else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_def(node, "method" if self._scope else "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_def(node, "class")

    # -- calls ---------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        """Classify every call site by what it COULD be.

        Only `internal` and `unresolved` belong in the resolution denominator.
        A `len()` or a `self.foo()` is not a missed cross-module edge, and
        counting it as one makes the map look blinder than it is.
        """
        func = node.func
        if isinstance(func, ast.Name):
            target = self.bindings.get(func.id)
            if target:
                cat = "internal"
            elif func.id in self.external_names:
                cat = "external"
            elif func.id in _BUILTINS:
                cat = "builtin"
            elif func.id in self.local_defs:
                cat = "local_method"      # defined in this very module
            else:
                cat = "unresolved"
            self.calls.append((target, func.id, func.id, node.lineno, cat))

        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                owner = func.value.id
                target = self.module_aliases.get(owner) or self.bindings.get(owner)
                raw = f"{owner}.{func.attr}"
                if target:
                    cat = "internal"
                elif owner in ("self", "cls", "super"):
                    cat = "local_method"
                elif owner in self.external_names:
                    cat = "external"
                else:
                    cat = "unresolved"
                self.calls.append((target, func.attr, raw, node.lineno, cat))
            else:
                # A call on an expression (`get_client().submit()`, `rows[0].x()`).
                # Static analysis cannot attribute these without type inference,
                # and they are overwhelmingly method calls on local objects.
                self.calls.append((None, func.attr, func.attr, node.lineno, "local_method"))
        self.generic_visit(node)


def build_graph(root: str | Path, skip_dirs: Optional[set[str]] = None,
                exclude: Optional[set[str]] = None) -> CodeGraph:
    """Crawl `root` and return a graph of AST facts only.

    No clustering and no naming happens here. Those are inference and live in
    `cluster.py` / the agent layer, so that this function's output can be
    diffed against the source and checked.
    """
    root = Path(root).resolve()
    graph = CodeGraph(root=str(root))
    cov = graph.coverage

    # `exclude` is for directories that are real code but not the architecture
    # you are trying to see. On the first real run, test modules were 400+ of
    # 620 and dominated the partition, so the top level was a map of the test
    # suite. They stay crawlable — this is a view choice, not a claim that they
    # do not exist.
    skip = set(skip_dirs if skip_dirs is not None else SKIP_DIRS) | set(exclude or ())
    files = list(iter_python_files(root, skip))
    cov.files_seen = len(files)

    # Pass 1 - what modules exist. Needed before imports can be resolved.
    file_modules: list[tuple[Path, str]] = []
    for p in files:
        try:
            file_modules.append((p, module_name_for(p, root)))
        except ValueError:
            continue
    internal = {m for _, m in file_modules}
    cov.modules = len(internal)

    # Pass 2 - parse each file.
    visitors: list[tuple[str, str, _ModuleVisitor]] = []
    for path, module in file_modules:
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError, OSError) as exc:
            cov.files_failed += 1
            cov.failures.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        cov.files_parsed += 1

        v = _ModuleVisitor(module, rel, internal)
        v.local_defs = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        v.visit(tree)
        visitors.append((module, rel, v))

        graph.nodes.append(Node(
            id=module,
            kind="module",
            label=module.rsplit(".", 1)[-1],
            path=rel,
            lineno=1,
            provenance=Provenance(kind="ast", evidence=(f"{rel}:1",), note="module file"),
            meta={"dotted": module, "loc": len(tree.body)},
        ))

    # Pass 3 - symbols, imports, calls.
    import_pairs: dict[tuple[str, str], list[str]] = {}
    call_pairs: dict[tuple[str, str], list[str]] = {}

    for module, rel, v in visitors:
        for qual, kind, lineno, end_lineno in v.defs:
            graph.nodes.append(Node(
                id=f"{module}:{qual}",
                kind="symbol",
                label=qual,
                parent=module,
                path=rel,
                lineno=lineno,
                end_lineno=end_lineno,
                provenance=Provenance(kind="ast", evidence=(f"{rel}:{lineno}",), note=kind),
                meta={"symbol_kind": kind, "module": module},
            ))

        for target, lineno in v.import_edges:
            if target == module or target not in internal:
                continue
            import_pairs.setdefault((module, target), []).append(f"{rel}:{lineno}")

        for target, symbol, raw, lineno, category in v.calls:
            cov.call_sites += 1
            if category == "builtin":
                cov.builtin_calls += 1
            elif category == "local_method":
                cov.local_method_calls += 1
            elif category == "external":
                cov.external_calls += 1
            elif target and target in internal and target != module:
                cov.resolved_calls += 1
                call_pairs.setdefault((module, target), []).append(f"{rel}:{lineno} {raw}()")
            elif target == module:
                cov.local_method_calls += 1  # module-internal, real but not an edge
            else:
                # Genuinely a candidate cross-module call we could not attribute.
                cov.unresolved_calls += 1

    for (src, dst), ev in sorted(import_pairs.items()):
        graph.edges.append(Edge(
            id=f"imports::{src}::{dst}",
            source=src, target=dst, kind="imports", weight=len(ev),
            provenance=Provenance(kind="ast", evidence=tuple(ev), note="import statement"),
        ))

    for (src, dst), ev in sorted(call_pairs.items()):
        graph.edges.append(Edge(
            id=f"calls::{src}::{dst}",
            source=src, target=dst, kind="calls", weight=len(ev),
            provenance=Provenance(kind="ast", evidence=tuple(ev), note="resolved call site"),
        ))

    return graph
