"""The crawler's ground-truth claims, made to fail first.

A map is a measurement. The failure mode of a measurement is not an error, it is
a plausible wrong value — and a diagram suppresses doubt better than a number
does. So the tests that matter here are not "does it find edges"; a crawler that
draws an edge between every pair of modules also finds edges.

The tests that matter are:

  1. DELETE A REAL EDGE FROM THE SOURCE AND THE MAP MUST CHANGE.
     If the graph renders the same with and without the call, the graph is
     decoration and nothing built on it means anything.

  2. AN UNRESOLVED CALL IS COUNTED, NOT DROPPED.
     Static analysis of Python always misses edges. The dangerous version of
     that is the one that reports a tidy architecture and says nothing.

  3. EVERY 'ast' FACT CARRIES EVIDENCE YOU CAN OPEN.
     An AST fact with no file:line is an inference wearing a fact's clothes.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cartograph.crawler.ast_index import build_graph, module_name_for
from cartograph.model import Provenance


def _write(root: Path, rel: str, src: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
    return p


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature codebase whose real edges we know by construction."""
    _write(tmp_path, "core/__init__.py", "")
    _write(tmp_path, "core/pnl.py", """
        def realized_pnl(rows):
            return sum(r["v"] for r in rows)

        class Book:
            def total(self):
                return 0
    """)
    _write(tmp_path, "core/broker.py", """
        def submit(order):
            return {"id": 1}
    """)
    _write(tmp_path, "core/operations.py", """
        from core.pnl import realized_pnl
        from core import broker

        def close_position(rows, order):
            value = realized_pnl(rows)
            broker.submit(order)
            return value
    """)
    _write(tmp_path, "cron/__init__.py", "")
    _write(tmp_path, "cron/cycle.py", """
        from core.operations import close_position

        def run(rows, order):
            return close_position(rows, order)
    """)
    return tmp_path


# --------------------------------------------------------------------------
# 1. The control: remove the edge, the map must lose it.
# --------------------------------------------------------------------------

def test_deleting_a_call_removes_the_edge(repo: Path):
    """The single check this whole tool stands on.

    `core.operations` calls `core.broker.submit`. Delete that one line and the
    edge must disappear. A graph that survives this deletion unchanged is not
    reading the source.
    """
    before = build_graph(repo)
    edge_ids = {e.id for e in before.edges_of("calls")}
    assert "calls::core.operations::core.broker" in edge_ids, (
        "baseline is wrong: the crawler never found the edge we are about to delete, "
        "so its later absence would prove nothing"
    )

    # Remove ONLY the call, leaving the import in place.
    ops = repo / "core" / "operations.py"
    ops.write_text(
        ops.read_text().replace("    broker.submit(order)\n", ""),
        encoding="utf-8",
    )

    after = build_graph(repo)
    after_ids = {e.id for e in after.edges_of("calls")}
    assert "calls::core.operations::core.broker" not in after_ids, (
        "the call edge survived deletion of the only call site — the map is not "
        "derived from the source"
    )
    # And the import edge, which we did NOT delete, must still be there. Without
    # this the test would also pass if the crawler simply stopped working.
    assert "imports::core.operations::core.broker" in {e.id for e in after.edges_of("imports")}


def test_deleting_a_module_removes_its_node_and_edges(repo: Path):
    before = build_graph(repo)
    assert before.node("core.broker") is not None

    (repo / "core" / "broker.py").unlink()

    after = build_graph(repo)
    assert after.node("core.broker") is None
    assert not [e for e in after.edges if e.target == "core.broker"], (
        "edges still point at a module whose file is gone"
    )


# --------------------------------------------------------------------------
# 2. Absence is counted, never silently dropped.
# --------------------------------------------------------------------------

def test_an_unresolvable_call_is_counted_not_dropped(repo: Path):
    """Dynamic dispatch must show up as a number, not as a tidy diagram."""
    baseline = build_graph(repo).coverage.unresolved_calls

    _write(repo, "core/dynamic.py", """
        import importlib

        def go(name, payload):
            mod = importlib.import_module(name)
            handler = getattr(mod, "handle")
            return handler(payload)
    """)

    cov = build_graph(repo).coverage
    assert cov.unresolved_calls > baseline, (
        "a getattr-dispatched call added no unresolved count — the crawler is "
        "hiding what it cannot see"
    )
    assert cov.call_resolution_rate < 1.0
    # No invented edge to a module that was never named statically.
    edges = {(e.source, e.target) for e in build_graph(repo).edges_of("calls")}
    assert not any(s == "core.dynamic" for s, _ in edges), (
        "the crawler guessed a target for a dynamically dispatched call"
    )


def test_coverage_reports_a_real_denominator(repo: Path):
    cov = build_graph(repo).coverage
    assert cov.files_seen == cov.files_parsed + cov.files_failed
    # Every call site lands in exactly one category. The resolution RATE is
    # reported over candidate cross-module calls only, because builtins and
    # self-calls can never be an inter-module edge and padding the denominator
    # with them understates coverage as surely as guessing inflates it.
    assert cov.call_sites == (
        cov.resolved_calls + cov.unresolved_calls
        + cov.builtin_calls + cov.local_method_calls + cov.external_calls
    ), "a call site was counted in no category, or in two"
    assert cov.candidate_calls == cov.resolved_calls + cov.unresolved_calls
    assert cov.builtin_calls > 0, "sum()/len() in the fixture were not seen as builtins"
    assert cov.call_sites > 0 and cov.modules > 0


def test_a_syntax_error_is_a_recorded_failure_not_a_silent_skip(repo: Path):
    _write(repo, "core/broken.py", "def oops(:\n    pass\n")
    cov = build_graph(repo).coverage
    assert cov.files_failed == 1
    assert any("broken.py" in f for f in cov.failures)
    assert cov.parse_rate < 1.0, "an unparseable file left the parse rate at 100%"


# --------------------------------------------------------------------------
# 3. Evidence is openable, and a fact without it is refused.
# --------------------------------------------------------------------------

def test_every_ast_element_carries_an_openable_file_line(repo: Path):
    g = build_graph(repo)
    assert g.nodes and g.edges

    for el in [*g.nodes, *g.edges]:
        p = el.provenance
        assert p.kind == "ast", f"{el.id} is not an AST fact but the crawler emitted it"
        assert p.evidence, f"{el.id} claims to be a fact with no evidence"
        for ev in p.evidence:
            rel = ev.split(":")[0]
            assert (repo / rel).exists(), f"{el.id} cites {rel}, which does not exist"


def test_an_ast_provenance_without_evidence_is_refused():
    """The type system carries the rule, not a convention someone remembers."""
    with pytest.raises(ValueError, match="evidence"):
        Provenance(kind="ast", evidence=())

    # Inference is allowed to have no file:line — that is what makes it inference.
    assert Provenance(kind="inferred", note="named by agent").evidence == ()


def test_provenance_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="ast.*inferred"):
        Provenance(kind="probably", evidence=("a.py:1",))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Resolution details that have to be right for the rest to mean anything.
# --------------------------------------------------------------------------

def test_from_import_binds_the_symbol_to_its_module(repo: Path):
    g = build_graph(repo)
    e = next(e for e in g.edges_of("calls")
             if e.source == "cron.cycle" and e.target == "core.operations")
    assert e.weight == 1
    assert any("cycle.py:" in ev and "close_position()" in ev for ev in e.provenance.evidence)


def test_a_lazy_import_inside_a_function_is_still_seen(repo: Path):
    """This codebase's `from core import x as _ol` idiom is an ordinary AST node."""
    _write(repo, "core/lazy.py", """
        def compute(rows):
            from core.pnl import realized_pnl as _rp
            return _rp(rows)
    """)
    g = build_graph(repo)
    ids = {e.id for e in g.edges_of("calls")}
    assert "calls::core.lazy::core.pnl" in ids, "a function-scoped import was missed"


def test_relative_imports_resolve(repo: Path):
    _write(repo, "core/relative.py", """
        from .pnl import realized_pnl

        def go(rows):
            return realized_pnl(rows)
    """)
    g = build_graph(repo)
    assert "calls::core.relative::core.pnl" in {e.id for e in g.edges_of("calls")}


def test_symbols_are_nested_under_their_module(repo: Path):
    g = build_graph(repo)
    syms = {n.id: n for n in g.nodes_of("symbol")}
    assert "core.pnl:realized_pnl" in syms
    assert syms["core.pnl:realized_pnl"].parent == "core.pnl"
    assert syms["core.pnl:Book.total"].meta["symbol_kind"] == "method"
    assert syms["core.pnl:realized_pnl"].lineno == 1


def test_external_imports_produce_no_nodes(repo: Path):
    """`import importlib` must not become a block in an architecture diagram."""
    _write(repo, "core/ext.py", "import json\nimport os\n\ndef f():\n    return json.dumps({})\n")
    g = build_graph(repo)
    assert g.node("json") is None and g.node("os") is None
    assert all(not n.id.startswith("json") for n in g.nodes)


def test_vendored_trees_are_skipped(repo: Path):
    _write(repo, "node_modules/pkg/thing.py", "def x():\n    return 1\n")
    _write(repo, ".venv/lib/dep.py", "def y():\n    return 2\n")
    g = build_graph(repo)
    assert not [n for n in g.nodes if "node_modules" in (n.path or "")]
    assert not [n for n in g.nodes if ".venv" in (n.path or "")]


def test_module_name_for_handles_packages(tmp_path: Path):
    assert module_name_for(tmp_path / "core" / "pnl.py", tmp_path) == "core.pnl"
    assert module_name_for(tmp_path / "core" / "__init__.py", tmp_path) == "core"
    assert module_name_for(tmp_path / "a" / "b" / "c.py", tmp_path) == "a.b.c"
