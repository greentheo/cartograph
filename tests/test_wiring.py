"""Wiring verdicts, tested against shapes with known answers.

The fixtures here are modelled on real defects rather than invented ones,
because a detector tested only on cases its author imagined will pass on those
and miss the field. `test_finds_the_conc_grid_shape` reproduces the exact
structure of a live finding: a grid constant defined once, never read, while
the value it names is hardcoded at a different line.

Every detector test is paired with a CONTROL — a case that must NOT fire. A
report that flags everything is worthless in the same way one that flags
nothing is, and the false-positive direction is worse here because it is the
one that trains a reader to stop looking.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cartograph.crawler.wiring import analyse_wiring


def _write(root: Path, rel: str, src: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
    return p


def _param(report, name):
    for p in report.parameters:
        if p.name == name:
            return p
    raise AssertionError(f"{name!r} not in report; saw {[p.name for p in report.parameters]}")


# --------------------------------------------------------------------------
# The shape that started this: a pin indistinguishable from a search.
# --------------------------------------------------------------------------

def test_finds_the_conc_grid_shape(tmp_path: Path):
    """A grid constant that is never read, beside a hardcoded value."""
    _write(tmp_path, "walkforward.py", """
        CONC_GRID = [2, 3, 5, 8, 12]
        DTE_GRID = [21, 29, 45]

        def run(tail_hedge, settings):
            conc = 2 if tail_hedge else 5          # hardcoded — CONC_GRID unused
            for dte in DTE_GRID:                   # DTE_GRID genuinely swept
                yield conc, dte
    """)

    report = analyse_wiring(tmp_path)

    dead = _param(report, "CONC_GRID")
    assert dead.verdict == "dead", f"CONC_GRID not flagged: {dead.verdict} / {dead.why}"
    assert dead.read_count == 0
    assert dead.defined_at == ["walkforward.py:1"]
    assert "[2, 3, 5, 8, 12]" in (dead.literal or "")

    # THE CONTROL. DTE_GRID is the same shape of name in the same file and IS
    # read. If this also came back dead the detector would be flagging
    # "is a constant", not "is unwired", and would be useless.
    live = _param(report, "DTE_GRID")
    assert live.verdict == "wired", "a genuinely-swept grid was flagged dead — false positive"
    assert live.read_count >= 1


def test_a_constant_read_only_by_another_module_is_wired(tmp_path: Path):
    """The false positive that would discredit the whole report."""
    _write(tmp_path, "settings.py", "MAX_RETRIES = 5\n")
    _write(tmp_path, "client.py", """
        from settings import MAX_RETRIES

        def go():
            for _ in range(MAX_RETRIES):
                pass
    """)
    report = analyse_wiring(tmp_path)
    p = _param(report, "MAX_RETRIES")
    assert p.verdict == "wired", (
        "a constant consumed by its importer was called dead — the analysis is "
        "per-module when it must be per-tree"
    )
    assert any("client.py" in r for r in p.read_at)


def test_dead_verdict_disappears_when_the_read_is_added(tmp_path: Path):
    """Make it fail first: add the missing read, the verdict must flip."""
    _write(tmp_path, "m.py", "GRID = [1, 2, 3]\n\ndef run():\n    return 7\n")
    assert _param(analyse_wiring(tmp_path), "GRID").verdict == "dead"

    _write(tmp_path, "m.py", "GRID = [1, 2, 3]\n\ndef run():\n    return sum(GRID)\n")
    assert _param(analyse_wiring(tmp_path), "GRID").verdict == "wired", (
        "the verdict did not respond to the source changing — it is not measuring reads"
    )


# --------------------------------------------------------------------------
# A config the run overwrites before it reads it.
# --------------------------------------------------------------------------

def test_config_key_written_and_read_is_flagged_as_mutated(tmp_path: Path):
    """The '#858' shape: the backtest overwrites the config it was handed."""
    _write(tmp_path, "engine.py", """
        def apply_structure(cfg, fitted):
            cfg["target_delta"] = fitted.delta      # written
            return cfg

        def price(cfg):
            return cfg["target_delta"] * 100        # read
    """)
    report = analyse_wiring(tmp_path)
    p = _param(report, "target_delta")
    assert p.verdict == "mutated_before_read"
    assert p.written_at and p.read_at
    assert "does not describe what was used" in p.why


def test_a_read_only_config_key_is_wired(tmp_path: Path):
    """Control for the above — reading a key is normal and must not warn."""
    _write(tmp_path, "r.py", "def go(cfg):\n    return cfg['symbol'], cfg.get('dte')\n")
    report = analyse_wiring(tmp_path)
    assert _param(report, "symbol").verdict == "wired"
    assert _param(report, "dte").verdict == "wired"


def test_a_write_only_key_is_reported_separately(tmp_path: Path):
    _write(tmp_path, "w.py", "def go(out):\n    out['never_read'] = 1\n")
    p = _param(analyse_wiring(tmp_path), "never_read")
    assert p.verdict == "write_only"
    assert p.read_at == []


# --------------------------------------------------------------------------
# A CLI flag you can pass that changes nothing.
# --------------------------------------------------------------------------

def test_an_unread_cli_flag_is_dead(tmp_path: Path):
    """`--budget` shape: declared, parseable, consumed by nothing."""
    _write(tmp_path, "cli.py", """
        import argparse

        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--budget", type=float)
            p.add_argument("--symbol")
            args = p.parse_args()
            return run(args.symbol)

        def run(symbol):
            return symbol
    """)
    report = analyse_wiring(tmp_path)

    dead = _param(report, "--budget")
    assert dead.verdict == "dead"
    assert "changes nothing" in dead.why

    # Control: --symbol is declared in the same way and IS read.
    assert _param(report, "--symbol").verdict == "wired"


def test_dashed_flag_names_map_to_underscored_attributes(tmp_path: Path):
    _write(tmp_path, "c.py", """
        import argparse

        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--max-loss")
            a = p.parse_args()
            return a.max_loss
    """)
    assert _param(analyse_wiring(tmp_path), "--max-loss").verdict == "wired"


# --------------------------------------------------------------------------
# The report must not lie about its own coverage.
# --------------------------------------------------------------------------

def test_unparseable_files_are_recorded_not_skipped(tmp_path: Path):
    _write(tmp_path, "ok.py", "A_CONST = 1\n\ndef f():\n    return A_CONST\n")
    _write(tmp_path, "bad.py", "def broken(:\n")
    report = analyse_wiring(tmp_path)
    assert report.files_failed == 1
    assert any("bad.py" in f for f in report.failures)
    assert report.files_parsed == 1


def test_private_and_short_names_are_not_treated_as_knobs(tmp_path: Path):
    """Control on the population: not every uppercase name is a parameter."""
    _write(tmp_path, "m.py", "_PRIVATE = 1\nID = 2\nREAL_KNOB = 3\n")
    names = {p.name for p in analyse_wiring(tmp_path).parameters if p.kind == "constant"}
    assert "REAL_KNOB" in names
    assert "_PRIVATE" not in names, "a private constant was treated as a control surface"
    assert "ID" not in names, "a two-letter name was treated as a control surface"


def test_every_verdict_carries_evidence_you_can_open(tmp_path: Path):
    _write(tmp_path, "m.py", "GRID = [1]\nOTHER = 2\n\ndef f():\n    return OTHER\n")
    report = analyse_wiring(tmp_path)
    for p in report.parameters:
        if p.kind == "constant":
            assert p.defined_at, f"{p.name} has a verdict with no definition site"
        assert p.why, f"{p.name} has verdict {p.verdict} with no stated reason"
        for site in [*p.defined_at, *p.read_at, *p.written_at]:
            rel = site.rsplit(":", 1)[0]
            assert (tmp_path / rel).exists(), f"{p.name} cites {rel}, which does not exist"


# --------------------------------------------------------------------------
# Regression: the false positive found on the first real run.
# --------------------------------------------------------------------------

def test_a_constant_read_as_a_qualified_module_attribute_is_wired(tmp_path: Path):
    """`jobs.CONCURRENCY_CAP` is a read. Missing it marked a live knob dead.

    Found by hand-checking the detector's output against the real codebase:
    CONCURRENCY_CAP was reported dead while runs.py:50 reads it through the
    module object. A false 'dead' is the direction that discredits the report,
    so it gets a permanent test.
    """
    _write(tmp_path, "web/__init__.py", "")
    _write(tmp_path, "web/jobs.py", "CONCURRENCY_CAP = 2\n")
    _write(tmp_path, "web/runs.py", """
        from web import jobs

        def can_start(running):
            return running < jobs.CONCURRENCY_CAP
    """)
    p = _param(analyse_wiring(tmp_path), "CONCURRENCY_CAP")
    assert p.verdict == "wired", "a constant read as module.CONST was called dead"
    assert any("runs.py" in r for r in p.read_at)


def test_a_coincidental_attribute_of_the_same_name_does_not_rescue_a_dead_constant(tmp_path: Path):
    """The control on the fix: the qualifier must match the defining module.

    Without this, the fix would mark every dead constant wired on any unrelated
    `obj.NAME` anywhere in the tree — trading a false positive for a silent
    false negative, which is worse.
    """
    _write(tmp_path, "settings.py", "TIMEOUT_SECS = 30\n")
    _write(tmp_path, "other.py", """
        def go(response):
            return response.TIMEOUT_SECS
    """)
    p = _param(analyse_wiring(tmp_path), "TIMEOUT_SECS")
    assert p.verdict == "dead", (
        "an attribute on an unrelated object was counted as a read of the constant"
    )
