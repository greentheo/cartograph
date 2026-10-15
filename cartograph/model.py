"""The graph model.

One rule governs this file: **every node and every edge carries its provenance,
and provenance has exactly two kinds.**

    ast       - derived mechanically from the source. Re-derivable, exact, and
                carries a file:line you can open. If the source changes, this
                changes. If it does not change, that is a bug in the crawler.

    inferred  - produced by a model. A name, a grouping, a summary. It may be
                useful and it may be wrong, and the UI renders it differently
                so a reader is never in doubt about which they are looking at.

The reason for the split is that a map is a measurement, and the failure mode
of a measurement here is not an error, it is a plausible wrong value. A diagram
suppresses doubt better than a number does. So the diagram must say, per element,
what it knows versus what it guessed.

Coverage is part of the model for the same reason: a graph that silently drops
the call sites it could not resolve reports a tidier architecture than the one
that exists. `Coverage` makes the blind spot a first-class number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

ProvKind = Literal["ast", "inferred"]
NodeKind = Literal["cluster", "module", "symbol"]
EdgeKind = Literal["imports", "calls", "contains"]


@dataclass(frozen=True)
class Provenance:
    """Why this element is in the graph.

    `evidence` for an `ast` element is a list of `path:line` strings pointing at
    the source that produced it. For an `inferred` element it names the producer
    (model id, prompt hash) so a reader can ask what generated the claim.
    """

    kind: ProvKind
    evidence: tuple[str, ...] = ()
    note: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in ("ast", "inferred"):
            raise ValueError(f"provenance kind must be 'ast' or 'inferred', got {self.kind!r}")
        if self.kind == "ast" and not self.evidence:
            # An AST fact with no evidence is indistinguishable from an inference
            # wearing a fact's clothes. That is the exact confusion this type exists
            # to prevent, so it is refused at construction.
            raise ValueError("an 'ast' provenance must carry at least one file:line evidence string")

    @property
    def is_fact(self) -> bool:
        return self.kind == "ast"


@dataclass
class Node:
    id: str
    kind: NodeKind
    label: str
    provenance: Provenance
    parent: Optional[str] = None
    path: Optional[str] = None
    lineno: Optional[int] = None
    end_lineno: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    id: str
    source: str
    target: str
    kind: EdgeKind
    provenance: Provenance
    weight: int = 1
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Coverage:
    """What the crawler could not account for.

    These numbers exist so the map can admit its own blind spots on screen.
    `unresolved_calls` is the important one: Python's dynamic dispatch, getattr
    and runtime imports mean a static crawler will always miss edges. A map that
    hides that count looks more complete than it is, and the reader has no way
    to know how much to trust an absence.
    """

    files_seen: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    modules: int = 0
    call_sites: int = 0
    resolved_calls: int = 0
    unresolved_calls: int = 0
    failures: list[str] = field(default_factory=list)

    # Call sites split by what they COULD have been, so the resolution rate has
    # an honest denominator. The first version of this metric divided internal
    # edges by every call site in the tree — including `len()`, `str()` and
    # `self.foo()`, none of which can ever be a cross-module edge. It reported
    # 11% and read as a catastrophic blind spot when most of the denominator was
    # never eligible. A rate whose denominator contains impossible cases
    # understates coverage exactly as reliably as a numerator inflated with
    # guesses overstates it.
    builtin_calls: int = 0
    local_method_calls: int = 0
    external_calls: int = 0

    @property
    def candidate_calls(self) -> int:
        """Sites that could plausibly be a cross-module edge: resolved + missed."""
        return self.resolved_calls + self.unresolved_calls

    @property
    def call_resolution_rate(self) -> float:
        """Of the calls that COULD be internal edges, how many were resolved."""
        return (self.resolved_calls / self.candidate_calls) if self.candidate_calls else 0.0

    @property
    def parse_rate(self) -> float:
        return (self.files_parsed / self.files_seen) if self.files_seen else 0.0


@dataclass
class CodeGraph:
    root: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)

    # -- lookups -------------------------------------------------------------

    def node(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def nodes_of(self, kind: NodeKind) -> list[Node]:
        return [n for n in self.nodes if n.kind == kind]

    def edges_of(self, kind: EdgeKind) -> list[Edge]:
        return [e for e in self.edges if e.kind == kind]

    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    def children(self, node_id: str) -> list[Node]:
        return [n for n in self.nodes if n.parent == node_id]

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "coverage": {
                **asdict(self.coverage),
                "candidate_calls": self.coverage.candidate_calls,
                "call_resolution_rate": round(self.coverage.call_resolution_rate, 4),
                "parse_rate": round(self.coverage.parse_rate, 4),
            },
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)
