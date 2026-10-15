"""FastAPI backend.

Holds one crawled graph per repository root, cached, with an explicit
`crawled_at` on every response. A map is an artifact and an artifact has a
build date — serving a cached graph without saying when it was built is how a
display comes to describe a state nobody is in any more.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as project_config
from . import languages
from .agent import claude as agent
from .crawler.cluster import apply_clusters
from .intent import Intent, intent_from_edit
from .roles import SeparationOfDutiesError

app = FastAPI(title="cartograph", version="0.1.0")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# (root, include_tests, resolution) -> (graph, wiring_report, crawled_at_epoch)
_CACHE: dict[tuple, tuple[Any, Any, float]] = {}


def _project(root: str):
    """Config + chosen language adapter for a root. Nothing is hardcoded."""
    p = Path(root).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(404, f"not a directory: {p}")
    try:
        cfg = project_config.load(p)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        det = languages.detect(p, prefer=cfg.language)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc))
    return p, cfg, det


def _load(root: str, refresh: bool = False, include_tests: bool = False,
          resolution: Optional[float] = None):
    p, cfg, det = _project(root)
    resolution = cfg.resolution if resolution is None else resolution
    key = (str(p), include_tests, round(resolution, 3))
    if refresh or key not in _CACHE:
        exclude = set() if include_tests else set(cfg.exclude)
        g = apply_clusters(det.adapter.build_graph(p, exclude=exclude),
                           resolution=resolution)
        # Wiring always covers the WHOLE tree, excluded dirs included: a constant
        # read only by a test is still read, and calling it dead would be false.
        w = det.adapter.analyse_wiring(p)
        _CACHE[key] = (g, w, time.time())
    g, w, ts = _CACHE[key]
    return g, w, ts, cfg, det


def _age(ts: float) -> dict[str, Any]:
    secs = time.time() - ts
    return {
        "crawled_at_epoch": ts,
        "age_seconds": round(secs, 1),
        "stale": secs > 300,  # the UI says so rather than quietly serving old facts
    }


# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "agent_available": agent.claude_available(),
        "cached_roots": list(_CACHE),
    }


@app.get("/api/graph")
def get_graph(root: str, refresh: bool = False, level: str = "cluster",
              include_tests: bool = False, resolution: Optional[float] = None,
              min_members: Optional[int] = None) -> dict[str, Any]:
    """The map. `level` selects cluster (top) or module (drilled) granularity.

    `resolution` tunes how coarsely blocks are drawn; `include_tests` decides
    whether the test suite is part of the architecture you are looking at. On
    this codebase tests are 400+ of 620 modules, so including them makes the
    top level a map of the test suite rather than of the system.
    """
    g, _w, ts, cfg, det = _load(root, refresh, include_tests, resolution)
    min_members = cfg.min_members if min_members is None else min_members
    data = g.to_dict()

    if level == "cluster":
        # A top level with a 60-block tail of single-module clusters is a
        # hairball, not an architecture. Small blocks are folded into one
        # honest "smaller blocks" node rather than dropped, so the module count
        # on screen still adds up to the tree.
        clusters = [n for n in data["nodes"] if n["kind"] == "cluster"]
        big = [n for n in clusters if n["meta"].get("member_count", 0) >= min_members]
        small = [n for n in clusters if n["meta"].get("member_count", 0) < min_members]
        keep = {n["id"] for n in big}
        nodes = list(big)
        if small:
            nodes.append({
                "id": "cluster:__small__", "kind": "cluster",
                "label": f"{len(small)} smaller blocks\n({sum(n['meta'].get('member_count', 0) for n in small)} modules)",
                "parent": None, "path": None, "lineno": None, "end_lineno": None,
                "provenance": {"kind": "inferred", "evidence": [],
                               "note": (f"{len(small)} clusters below the {min_members}-module "
                                        f"threshold, folded together for legibility. Lower the "
                                        f"threshold or open Modules to see them individually.")},
                "meta": {"members": [m for n in small for m in n["meta"].get("members", [])],
                         "member_count": sum(n["meta"].get("member_count", 0) for n in small),
                         "folded": True, "named_by": "mechanical"},
            })
        # Only CALL edges at the top level: an import edge between the same two
        # blocks says the same thing twice and doubles the visual density.
        edges = [e for e in data["edges"]
                 if e["kind"] == "calls" and e["source"] in keep and e["target"] in keep]
    elif level == "module":
        keep = {n["id"] for n in data["nodes"] if n["kind"] in ("module", "cluster")}
        nodes = [n for n in data["nodes"] if n["kind"] in ("module", "cluster")]
        edges = [e for e in data["edges"]
                 if e["source"] in keep and e["target"] in keep
                 and not e.get("meta", {}).get("rollup")]
    else:
        nodes, edges = data["nodes"], data["edges"]

    return {"root": data["root"], "level": level, "nodes": nodes, "edges": edges,
            "coverage": data["coverage"], "include_tests": include_tests,
            "resolution": resolution if resolution is not None else cfg.resolution,
            "language": det.adapter.name,
            "language_confidence": round(det.confidence, 3),
            # Say what the map does NOT cover. A map over 60% of a repo that
            # mentions nothing reads as complete.
            "unmapped_fraction": round(det.unmapped_fraction, 3),
            "is_mixed": det.is_mixed,
            **_age(ts)}


@app.get("/api/node/{node_id:path}")
def get_node(node_id: str, root: str, include_tests: bool = False,
             resolution: Optional[float] = None) -> dict[str, Any]:
    """One block, its children, and every edge touching it — with evidence."""
    g, _w, ts, _cfg, _det = _load(root, False, include_tests, resolution)
    node = g.node(node_id)
    if node is None:
        raise HTTPException(404, f"no node {node_id!r}")
    from dataclasses import asdict
    return {
        "node": asdict(node),
        "children": [asdict(c) for c in g.children(node_id)],
        "out_edges": [asdict(e) for e in g.out_edges(node_id)],
        "in_edges": [asdict(e) for e in g.in_edges(node_id)],
        **_age(ts),
    }


@app.get("/api/wiring")
def get_wiring(root: str, verdict: Optional[str] = None,
               refresh: bool = False) -> dict[str, Any]:
    """Which parameters are actually connected to anything."""
    _g, w, ts, _cfg, _det = _load(root, refresh)
    data = w.to_dict()
    if verdict:
        data["parameters"] = [p for p in data["parameters"] if p["verdict"] == verdict]
    return {**data, **_age(ts)}


@app.get("/api/source")
def get_source(root: str, path: str, start: int = 1, end: int = 0) -> dict[str, Any]:
    """Read a span so a citation is one click from the code it points at."""
    base = Path(root).expanduser().resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "path escapes the crawl root")
    if not target.is_file():
        raise HTTPException(404, f"no file {path}")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    end = end or min(len(lines), start + 80)
    start = max(1, start)
    return {
        "path": path, "start": start, "end": min(end, len(lines)),
        "total_lines": len(lines),
        "lines": lines[start - 1:end],
    }


class AskBody(BaseModel):
    root: str
    question: str
    focus: Optional[str] = None
    model: Optional[str] = None
    role: Optional[str] = None
    capability: str = "investigate"


@app.post("/api/ask")
def ask(body: AskBody) -> dict[str, Any]:
    g, _w, _ts, cfg, _det = _load(body.root)
    ctx = agent.graph_context(g, focus=body.focus)

    role = None
    if body.role:
        try:
            role = cfg.roles.get(body.role)
            # Refuse here rather than letting the model decide whether it is
            # allowed. A seat that can talk itself into a capability does not
            # have a capability model, it has a suggestion.
            cfg.roles.assert_can(body.role, body.capability)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except SeparationOfDutiesError as exc:
            raise HTTPException(403, str(exc))

    try:
        answer = agent.ask(body.question, cwd=body.root, context=ctx,
                           model=body.model, role=role)
    except agent.AgentUnavailable as exc:
        raise HTTPException(503, str(exc))
    return answer.to_dict()


@app.get("/api/project")
def get_project(root: str) -> dict[str, Any]:
    """Config, detected language, and the roster in force for this repo."""
    p, cfg, det = _project(root)
    return {
        **cfg.to_dict(),
        "language_detected": det.adapter.name,
        "language_confidence": round(det.confidence, 3),
        "unmapped_fraction": round(det.unmapped_fraction, 3),
        "is_mixed": det.is_mixed,
        "adapters": languages.available(),
        "example_config": project_config.EXAMPLE,
    }


class SeparationBody(BaseModel):
    root: str
    actions: list[tuple[str, str, str]]     # (role, capability, subject)


@app.post("/api/separation")
def check_separation(body: SeparationBody) -> dict[str, Any]:
    """Did one seat both raise and resolve the same thing?

    The one invariant no single agent can check about itself.
    """
    _p, cfg, _det = _project(body.root)
    violations = cfg.roles.check_separation(body.actions)
    return {"violations": violations, "clean": not violations,
            "checked": len(body.actions)}


class EditBody(BaseModel):
    root: str
    kind: str                      # add_module | add_edge | add_symbol
    name: str
    called_by: list[str] = []
    calls: list[str] = []
    cluster: Optional[str] = None
    purpose: str = ""


@app.post("/api/intent")
def make_intent(body: EditBody) -> dict[str, Any]:
    """A diagram edit becomes a reviewable spec. It does not become code here.

    Nothing is written to the repository by this endpoint. The operator reads
    the intent as prose, amends it, and only then hands it to an agent — which
    is the whole point of choosing specs over direct codegen.
    """
    g, _w, _ts, cfg, _det = _load(body.root)
    intent: Intent = intent_from_edit(
        graph=g, kind=body.kind, name=body.name,
        called_by=body.called_by, calls=body.calls,
        cluster=body.cluster, purpose=body.purpose,
    )
    return intent.to_dict()


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/")
def index() -> FileResponse:  # pragma: no cover - served by StaticFiles
    return FileResponse(str(WEB_DIR / "index.html"))
