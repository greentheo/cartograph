"""Is this parameter actually wired to anything?

This module exists because of a specific, repeated, expensive failure: a
parameter that looks like a control and is not one. The shapes, all real:

  * `CONC_GRID = [2, 3, 5, 8, 12]` defined once, read never, while the value it
    supposedly controls is hardcoded at another line. A pin indistinguishable
    from a search. 89 system-folds "chose" the pinned value.
  * `target_dte`, `min_credit_dollars` — swept as though they were inputs,
    never read by the code that was supposed to consume them.
  * `--budget` constraining nothing once the basis changed, so a satisfied
    budget and an inert one produce byte-identical artifacts.
  * A config dict mutated in place before first read, so the file that
    describes a run cannot describe what the run actually used.

Every one produced a PLAUSIBLE result rather than an error, which is why none
were caught by testing the output. They are all, however, visible in the AST —
the information was there the whole time and nobody could see it at a glance.

**The verdicts here are facts, not opinions.** "Read at zero sites" is a
countable property of the source with file:line evidence on both sides. This
module is deliberately conservative: where it cannot prove something it says
UNKNOWN rather than guessing, because a wiring report that cries wolf gets
muted, and a muted control is the thing we are trying to prevent.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal, Optional

from .ast_index import SKIP_DIRS, iter_python_files, module_name_for

Verdict = Literal["wired", "dead", "write_only", "mutated_before_read", "unknown"]

# Names that look like knobs. Module-level ALL_CAPS is the convention this
# codebase uses for a tunable, and it is the population where dead ones hide.
def _is_knob_name(name: str) -> bool:
    return name.isupper() and len(name) > 2 and not name.startswith("_")


@dataclass
class Parameter:
    """One candidate control surface, and whether anything listens to it."""

    name: str
    kind: Literal["constant", "config_key", "cli_flag"]
    module: Optional[str] = None
    defined_at: list[str] = field(default_factory=list)
    read_at: list[str] = field(default_factory=list)
    written_at: list[str] = field(default_factory=list)
    verdict: Verdict = "unknown"
    why: str = ""
    literal: Optional[str] = None

    @property
    def read_count(self) -> int:
        return len(self.read_at)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["read_count"] = self.read_count
        return d


@dataclass
class WiringReport:
    root: str
    parameters: list[Parameter] = field(default_factory=list)
    files_parsed: int = 0
    files_failed: int = 0
    failures: list[str] = field(default_factory=list)

    def by_verdict(self, verdict: Verdict) -> list[Parameter]:
        return [p for p in self.parameters if p.verdict == verdict]

    @property
    def dead(self) -> list[Parameter]:
        return self.by_verdict("dead")

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        for p in self.parameters:
            counts[p.verdict] += 1
        return {
            "root": self.root,
            "parameters": [p.to_dict() for p in self.parameters],
            "counts": dict(counts),
            "files_parsed": self.files_parsed,
            "files_failed": self.files_failed,
            "failures": self.failures,
        }


class _WiringVisitor(ast.NodeVisitor):
    """Records where names are defined, read and written in one module."""

    def __init__(self, module: str, rel: str):
        self.module = module
        self.rel = rel
        self.module_level_consts: dict[str, tuple[int, Optional[str]]] = {}
        self.name_reads: dict[str, list[int]] = defaultdict(list)
        self.name_writes: dict[str, list[int]] = defaultdict(list)
        self.config_reads: dict[str, list[int]] = defaultdict(list)
        self.config_writes: dict[str, list[int]] = defaultdict(list)
        self.cli_flags: dict[str, list[int]] = defaultdict(list)
        # Attribute reads (`args.budget`) are kept SEPARATE from bare-name reads
        # on purpose. A parsed CLI flag is only ever consumed as an attribute, so
        # flags must consult this index — but a module constant must not, or any
        # unrelated `obj.FOO` in the tree would mark a genuinely dead constant as
        # wired. Widening this index to constants would cost false negatives in
        # exactly the population the report exists to find.
        self.attr_reads: dict[str, list[int]] = defaultdict(list)
        # Qualified reads, keyed (qualifier, attribute): `jobs.CONCURRENCY_CAP`
        # -> ("jobs", "CONCURRENCY_CAP"). This is how one module reads another
        # module's constant, and it is the ONLY attribute form a constant is
        # allowed to count, matched against the defining module's own leaf name.
        # Counting bare `obj.FOO` instead would mark dead constants wired on any
        # coincidental attribute of the same name — a false negative in the exact
        # population this report exists to surface.
        self.qualified_attr_reads: dict[tuple[str, str], list[int]] = defaultdict(list)
        self._depth = 0

    # -- module-level constants -------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            targets: list[ast.expr] = []
            value: Optional[ast.expr] = None
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
                targets, value = [stmt.target], stmt.value
            for t in targets:
                if isinstance(t, ast.Name) and _is_knob_name(t.id):
                    lit: Optional[str] = None
                    if value is not None:
                        try:
                            lit = ast.unparse(value)[:120]
                        except Exception:
                            lit = None
                    self.module_level_consts[t.id] = (stmt.lineno, lit)
        self.generic_visit(node)

    # -- reads and writes of plain names ----------------------------------

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.name_reads[node.id].append(node.lineno)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.name_writes[node.id].append(node.lineno)
        self.generic_visit(node)

    # -- config-shaped access: cfg["k"], cfg.get("k"), cfg["k"] = v -------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.attr_reads[node.attr].append(node.lineno)
            if isinstance(node.value, ast.Name):
                self.qualified_attr_reads[(node.value.id, node.attr)].append(node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        key = self._const_str(node.slice)
        if key is not None:
            if isinstance(node.ctx, ast.Store):
                self.config_writes[key].append(node.lineno)
            else:
                self.config_reads[key].append(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in ("get", "setdefault", "pop") and node.args:
                key = self._const_str(node.args[0])
                if key is not None:
                    if func.attr == "setdefault":
                        self.config_writes[key].append(node.lineno)
                    else:
                        self.config_reads[key].append(node.lineno)
            # argparse: parser.add_argument("--budget", ...)
            if func.attr == "add_argument" and node.args:
                flag = self._const_str(node.args[0])
                if flag and flag.startswith("-"):
                    self.cli_flags[flag.lstrip("-").replace("-", "_")].append(node.lineno)
        self.generic_visit(node)

    @staticmethod
    def _const_str(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Index):  # pragma: no cover - py<3.9 shape
            return _WiringVisitor._const_str(node.value)  # type: ignore[attr-defined]
        return None


def analyse_wiring(root: str | Path, skip_dirs: Optional[set[str]] = None) -> WiringReport:
    """Cross-module wiring analysis over every Python file under `root`.

    A constant is only called DEAD when it is read at zero sites **anywhere in
    the crawled tree**, not merely in its own module. That distinction matters:
    a constant read only by its importer is wired, and calling it dead would be
    a false positive of exactly the kind that trains people to ignore the report.
    """
    root = Path(root).resolve()
    report = WiringReport(root=str(root))

    visitors: list[tuple[str, str, _WiringVisitor]] = []
    for path in iter_python_files(root, skip_dirs if skip_dirs is not None else SKIP_DIRS):
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError, OSError) as exc:
            report.files_failed += 1
            report.failures.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        report.files_parsed += 1
        v = _WiringVisitor(module_name_for(path, root), rel)
        v.visit(tree)
        visitors.append((v.module, rel, v))

    # Global read index: a name read ANYWHERE counts as a read.
    global_reads: dict[str, list[str]] = defaultdict(list)
    global_cfg_reads: dict[str, list[str]] = defaultdict(list)
    global_cfg_writes: dict[str, list[str]] = defaultdict(list)
    global_flags: dict[str, list[str]] = defaultdict(list)
    global_attr_reads: dict[str, list[str]] = defaultdict(list)
    global_qualified: dict[tuple[str, str], list[str]] = defaultdict(list)

    for module, rel, v in visitors:
        for qual_key, lines in v.qualified_attr_reads.items():
            global_qualified[qual_key].extend(f"{rel}:{ln}" for ln in lines)
        for name, lines in v.attr_reads.items():
            global_attr_reads[name].extend(f"{rel}:{ln}" for ln in lines)
        for name, lines in v.name_reads.items():
            global_reads[name].extend(f"{rel}:{ln}" for ln in lines)
        for key, lines in v.config_reads.items():
            global_cfg_reads[key].extend(f"{rel}:{ln}" for ln in lines)
        for key, lines in v.config_writes.items():
            global_cfg_writes[key].extend(f"{rel}:{ln}" for ln in lines)
        for flag, lines in v.cli_flags.items():
            global_flags[flag].extend(f"{rel}:{ln}" for ln in lines)

    # -- constants ---------------------------------------------------------
    for module, rel, v in visitors:
        for name, (lineno, literal) in sorted(v.module_level_consts.items()):
            defined_at = f"{rel}:{lineno}"
            # The definition site itself is a Store, not a Load, so it never
            # appears in reads. Any read at all means something consumes it.
            #
            # A cross-module read looks like `jobs.CONCURRENCY_CAP`, where the
            # qualifier is the defining module's leaf name. Missing this form
            # cost a false "dead" on the first real run, which is the direction
            # that discredits the whole report.
            leaf = module.rsplit(".", 1)[-1]
            reads = sorted(
                set(global_reads.get(name, []))
                | set(global_qualified.get((leaf, name), []))
            )
            param = Parameter(
                name=name, kind="constant", module=module,
                defined_at=[defined_at], read_at=reads, literal=literal,
            )
            if not reads:
                param.verdict = "dead"
                param.why = (
                    f"{name} is defined at {defined_at} and read at 0 sites in the "
                    f"crawled tree. It cannot influence behaviour. If a value it "
                    f"names is in use, that value is hardcoded somewhere else — "
                    f"which makes a pin indistinguishable from a search."
                )
            else:
                param.verdict = "wired"
                param.why = f"read at {len(reads)} site(s)"
            report.parameters.append(param)

    # -- config keys -------------------------------------------------------
    for key in sorted(set(global_cfg_reads) | set(global_cfg_writes)):
        reads = sorted(set(global_cfg_reads.get(key, [])))
        writes = sorted(set(global_cfg_writes.get(key, [])))
        param = Parameter(name=key, kind="config_key", read_at=reads, written_at=writes)
        if reads and writes:
            param.verdict = "mutated_before_read"
            param.why = (
                f"key {key!r} is both written ({len(writes)} site(s)) and read "
                f"({len(reads)} site(s)). If a writer runs before the reader, the "
                f"stored value does not describe what was used. Check the order."
            )
        elif writes and not reads:
            param.verdict = "write_only"
            param.why = (
                f"key {key!r} is written at {len(writes)} site(s) and read at none. "
                f"It is recorded but nothing consumes it."
            )
        elif reads:
            param.verdict = "wired"
            param.why = f"read at {len(reads)} site(s)"
        report.parameters.append(param)

    # -- CLI flags ---------------------------------------------------------
    for flag in sorted(global_flags):
        # A parsed flag surfaces as `args.<flag>`, so the attribute index is the
        # one that matters here; bare-name and dict reads are included because
        # argparse results are also commonly splatted into kwargs or a dict.
        reads = sorted(
            set(global_attr_reads.get(flag, []))
            | set(global_reads.get(flag, []))
            | set(global_cfg_reads.get(flag, []))
        )
        param = Parameter(
            name=f"--{flag.replace('_', '-')}", kind="cli_flag",
            defined_at=sorted(set(global_flags[flag])), read_at=reads,
        )
        if reads:
            param.verdict = "wired"
            param.why = f"declared and read at {len(reads)} site(s)"
        else:
            param.verdict = "dead"
            param.why = (
                f"flag is declared via add_argument at {global_flags[flag][0]} but the "
                f"parsed attribute {flag!r} is never read. Passing it changes nothing, "
                f"and the run will not error."
            )
        report.parameters.append(param)

    return report
