"""Grouping modules into the blocks you actually see at the top level.

This is the inference layer, and it is kept in a separate file from
`ast_index.py` on purpose: everything here is a judgement, and judgements must
be quarantined from facts so the UI can render them differently.

**What is inferred and what is not, precisely:**

  * WHICH MODULES SHARE A BLOCK is inferred. Modularity optimisation over the
    real dependency graph. Deterministic and re-runnable, but
    still a judgement about where a boundary lies — reasonable people and
    reasonable resolutions would draw it elsewhere. Cluster nodes therefore
    carry `Provenance(kind="inferred")` and the UI dashes them.

  * WHAT A BLOCK IS CALLED is inferred, and more weakly than the grouping —
    a model reads the members and proposes a name. Until an agent names them,
    clusters carry a mechanical label derived from their members, which is
    honest but ugly. Never let a pretty name imply a verified concept.

  * THE ARROWS BETWEEN BLOCKS ARE FACTS. An arrow from block A to block B is a
    rollup of real call sites with real file:line evidence, so it stays
    `Provenance(kind="ast")` and renders solid. What is uncertain is which box
    a module sits in, not whether the calls happen. Drilling an arrow shows the
    underlying sites, and every one of them is checkable.

That asymmetry is the whole design: the boxes are a story, the arrows are
evidence, and you can always see which is which.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..model import CodeGraph, Edge, Node, Provenance

CLUSTER_PREFIX = "cluster:"


def _undirected_weights(graph: CodeGraph) -> dict[str, dict[str, int]]:
    """Symmetric adjacency over module-level import and call edges."""
    adj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    module_ids = {n.id for n in graph.nodes_of("module")}
    for e in graph.edges:
        if e.kind not in ("imports", "calls"):
            continue
        if e.source not in module_ids or e.target not in module_ids:
            continue
        adj[e.source][e.target] += e.weight
        adj[e.target][e.source] += e.weight
    return adj


def _seed_label(module_id: str, path: Optional[str]) -> str:
    """Directory is the prior. It is usually a decent first guess at intent."""
    if path and "/" in path:
        return path.split("/")[0]
    return module_id.split(".")[0]


def assign_clusters(graph: CodeGraph, max_iter: int = 12,
                    resolution: float = 1.0) -> dict[str, str]:
    """Modularity-optimising communities over the real dependency graph.

    **This replaced plain label propagation, which failed visibly on the first
    real codebase**: 497 of 620 modules collapsed into a single block. That is
    label propagation's known behaviour on a densely-connected graph, and the
    result was not a wrong diagram so much as a useless one — a top level with
    one box on it tells you nothing you did not already know.

    Modularity compares observed coupling against what you would expect by
    chance given each module's degree, so a hub that everything imports does not
    drag the whole tree into its community. `resolution` above 1.0 yields more,
    smaller blocks; below 1.0, fewer and larger.

    Deterministic: modules are visited in sorted order and every tie breaks
    lexicographically, so the same tree always produces the same partition. A
    grouping that shuffled between runs would make screenshots, saved views and
    agent answers all non-reproducible.
    """
    modules = sorted(n.id for n in graph.nodes_of("module"))
    paths = {n.id: n.path for n in graph.nodes_of("module")}
    adj = _undirected_weights(graph)

    # Seed each module in its own community, but remember the directory so an
    # isolated module (no edges at all) still lands somewhere meaningful.
    comm: dict[str, str] = {m: m for m in modules}
    degree: dict[str, int] = {m: sum(adj.get(m, {}).values()) for m in modules}
    total_w = sum(degree.values()) or 1
    comm_degree: dict[str, int] = {m: degree[m] for m in modules}

    for _ in range(max_iter):
        moved = False
        for m in modules:
            neighbours = adj.get(m)
            if not neighbours:
                continue
            here = comm[m]
            comm_degree[here] -= degree[m]

            # Weight from m into each candidate community.
            links: dict[str, int] = defaultdict(int)
            for nb, w in neighbours.items():
                links[comm[nb]] += w

            best, best_gain = here, None
            for cand in sorted(links):
                # Modularity gain: observed links in, minus the expectation from
                # degree alone. The subtraction is what stops a hub swallowing
                # the graph.
                gain = links[cand] - resolution * degree[m] * comm_degree.get(cand, 0) / (2.0 * total_w)
                if best_gain is None or gain > best_gain + 1e-12:
                    best, best_gain = cand, gain

            comm[m] = best
            comm_degree[best] = comm_degree.get(best, 0) + degree[m]
            if best != here:
                moved = True
        if not moved:
            break

    # Name each community after the directory most of its members live in, so
    # the mechanical label is at least recognisable before an agent renames it.
    members: dict[str, list[str]] = defaultdict(list)
    for m, c in comm.items():
        members[c].append(m)

    labels: dict[str, str] = {}
    used: dict[str, int] = defaultdict(int)
    for c, mods in sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        tally: dict[str, int] = defaultdict(int)
        for m in mods:
            tally[_seed_label(m, paths.get(m))] += 1
        stem = max(sorted(tally.items()), key=lambda kv: (kv[1], kv[0]))[0]
        used[stem] += 1
        name = stem if used[stem] == 1 else f"{stem}-{used[stem]}"
        for m in mods:
            labels[m] = name

    for m in modules:
        labels.setdefault(m, _seed_label(m, paths.get(m)))
    return labels


def _mechanical_label(members: list[str]) -> str:
    """A truthful, unlovely name used until an agent proposes a better one."""
    if not members:
        return "empty"
    heads = sorted({m.split(".")[0] for m in members})
    stem = "+".join(heads[:2]) + ("…" if len(heads) > 2 else "")
    return f"{stem} ({len(members)})"


def apply_clusters(graph: CodeGraph, labels: Optional[dict[str, str]] = None,
                   max_iter: int = 12, resolution: float = 1.0) -> CodeGraph:
    """Add cluster nodes, reparent modules, and roll edges up to cluster level.

    Mutates and returns `graph`. Module-level nodes and edges are preserved
    untouched — the cluster layer is additive, so drilling down never needs a
    re-crawl and the facts underneath a block are always one hop away.
    """
    labels = labels if labels is not None else assign_clusters(
        graph, max_iter=max_iter, resolution=resolution)

    members: dict[str, list[str]] = defaultdict(list)
    for module_id, label in labels.items():
        members[label].append(module_id)

    # -- cluster nodes: INFERRED ------------------------------------------
    for label, mods in sorted(members.items()):
        cid = f"{CLUSTER_PREFIX}{label}"
        graph.nodes.append(Node(
            id=cid,
            kind="cluster",
            label=_mechanical_label(sorted(mods)),
            provenance=Provenance(
                kind="inferred",
                note=("modularity-optimising communities over the real AST import/call "
                      "graph — the boundary is a judgement, not a fact"),
            ),
            meta={
                "members": sorted(mods),
                "member_count": len(mods),
                "seed": label,
                "named_by": "mechanical",
            },
        ))
        for m in mods:
            node = graph.node(m)
            if node is not None:
                node.parent = cid

    # -- cluster edges: FACTS, rolled up ----------------------------------
    module_to_cluster = {m: f"{CLUSTER_PREFIX}{l}" for m, l in labels.items()}
    rollup: dict[tuple[str, str, str], list[Edge]] = defaultdict(list)

    for e in list(graph.edges):
        if e.kind not in ("imports", "calls"):
            continue
        cs, ct = module_to_cluster.get(e.source), module_to_cluster.get(e.target)
        if not cs or not ct or cs == ct:
            continue  # intra-cluster detail belongs to the drill-down view
        rollup[(cs, ct, e.kind)].append(e)

    for (cs, ct, kind), member_edges in sorted(rollup.items()):
        evidence: tuple[str, ...] = tuple(
            ev for me in member_edges for ev in me.provenance.evidence
        )
        graph.edges.append(Edge(
            id=f"{kind}::{cs}::{ct}",
            source=cs, target=ct, kind=kind,
            weight=sum(me.weight for me in member_edges),
            provenance=Provenance(
                kind="ast",
                evidence=evidence[:200],
                note=(f"rollup of {len(member_edges)} module-level {kind} edges; "
                      f"every underlying site is a real file:line"),
            ),
            meta={
                "rollup": True,
                "rollup_of": [me.id for me in member_edges],
                "site_count": len(evidence),
                "truncated_evidence": len(evidence) > 200,
            },
        ))

    return graph


def rename_cluster(graph: CodeGraph, cluster_id: str, name: str, *,
                   rationale: str, named_by: str) -> Node:
    """Attach an agent-proposed name, keeping the mechanical one visible.

    The old label is retained in `meta["mechanical_label"]` so a reader can
    always see what the grouping looked like before a model made it readable.
    A name is the single easiest place for a confident wrong story to enter a
    diagram, so the provenance note records who proposed it and why.
    """
    node = graph.node(cluster_id)
    if node is None or node.kind != "cluster":
        raise KeyError(f"no cluster {cluster_id!r}")

    node.meta["mechanical_label"] = node.meta.get("mechanical_label", node.label)
    node.meta["named_by"] = named_by
    node.meta["name_rationale"] = rationale
    node.label = name
    node.provenance = Provenance(
        kind="inferred",
        note=f"name proposed by {named_by}: {rationale}",
    )
    return node
