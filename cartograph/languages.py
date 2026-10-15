"""The language seam.

cartograph is not a Python tool that happens to be pointed at Python. The
crawler, the wiring analyser and the graph model are all expressed in terms a
`LanguageAdapter` supplies, so **adding a language means writing one class**,
not editing anything above this line.

The seam is drawn at the honest place. Everything above it — provenance, the
fact/inference split, coverage, clustering, roles, intents — is about *reasoning
over a dependency graph* and has nothing to do with syntax. Everything below it
— how you find a module, what an import looks like, whether a constant is a
constant — is irreducibly per-language.

Two rules bind every adapter, and they are the ones that make the map worth
reading:

  1. **Emit no edge you cannot cite.** An unresolved reference is COUNTED
     (`Coverage.unresolved_calls`), never guessed into a plausible arrow.
  2. **Put impossible cases outside the denominator.** A language's builtins and
     local method calls can never be cross-module edges; counting them makes
     coverage look worse than it is, which discredits the real number.

`detect()` returns a confidence rather than a boolean because real repositories
are mixed. A repo that is 90% Python and 10% shell should map as Python and say
so, not refuse.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .crawler import ast_index, wiring
from .model import CodeGraph


@runtime_checkable
class LanguageAdapter(Protocol):
    """What cartograph needs from a language to map it."""

    name: str
    extensions: tuple[str, ...]

    def detect(self, root: Path) -> float:
        """Confidence in [0, 1] that this adapter should map `root`."""
        ...

    def build_graph(self, root: Path, exclude: Optional[set[str]] = None) -> CodeGraph:
        """Modules, definitions, and cited import/call edges."""
        ...

    def analyse_wiring(self, root: Path,
                       exclude: Optional[set[str]] = None) -> "wiring.WiringReport":
        """Which declared parameters are actually read."""
        ...


def _file_census(root: Path, extensions: tuple[str, ...]) -> tuple[int, int]:
    """(matching files, total source-ish files) — cheap and bounded."""
    matching = total = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in ast_index.SKIP_DIRS for part in p.parts):
            continue
        suf = p.suffix.lower()
        if suf in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java"):
            total += 1
            if suf in extensions:
                matching += 1
        if total > 4000:      # enough to decide; do not walk a monorepo twice
            break
    return matching, total


@dataclass
class PythonAdapter:
    """Python, via the stdlib AST. The reference implementation of the seam."""

    name: str = "python"
    extensions: tuple[str, ...] = (".py",)

    def detect(self, root: Path) -> float:
        matching, total = _file_census(root, self.extensions)
        return (matching / total) if total else 0.0

    def build_graph(self, root: Path, exclude: Optional[set[str]] = None) -> CodeGraph:
        return ast_index.build_graph(root, exclude=exclude)

    def analyse_wiring(self, root: Path,
                       exclude: Optional[set[str]] = None) -> "wiring.WiringReport":
        # Wiring deliberately ignores `exclude`: a constant read only by a test
        # is still read, and calling it dead because tests were hidden from the
        # architecture view would be a false positive of the worst kind.
        return wiring.analyse_wiring(root)


_ADAPTERS: list[LanguageAdapter] = [PythonAdapter()]


def register(adapter: LanguageAdapter) -> None:
    """Add an adapter. Later registrations win ties, so a project can override."""
    _ADAPTERS.insert(0, adapter)


def available() -> list[str]:
    return [a.name for a in _ADAPTERS]


@dataclass
class Detection:
    adapter: LanguageAdapter
    confidence: float
    others: dict[str, float]

    @property
    def unmapped_fraction(self) -> float:
        """Share of source files the chosen adapter does NOT cover."""
        return max(0.0, 1.0 - self.confidence)

    @property
    def is_mixed(self) -> bool:
        """True when a real amount of the repo is not covered by this adapter.

        Derived from the CHOSEN adapter's own coverage, not from the scores of
        other registered adapters. The first version compared registered
        adapters, so with only Python registered a repo that was a third
        TypeScript reported as unmixed — the tool could only notice languages it
        could already read, which is precisely backwards. What matters is the
        share it cannot see, and that is knowable without an adapter for it.

        A map covering 60% of a codebase and saying nothing reads as complete.
        """
        return self.unmapped_fraction >= 0.15


def detect(root: str | Path, prefer: Optional[str] = None) -> Detection:
    """Choose an adapter for `root`, or raise with what was actually found."""
    root = Path(root).expanduser().resolve()
    scores = {a.name: a.detect(root) for a in _ADAPTERS}

    if prefer and prefer != "auto":
        for a in _ADAPTERS:
            if a.name == prefer:
                return Detection(a, scores.get(prefer, 0.0), scores)
        raise KeyError(f"no adapter named {prefer!r}; have {', '.join(available())}")

    best = max(_ADAPTERS, key=lambda a: scores.get(a.name, 0.0))
    if scores.get(best.name, 0.0) <= 0.0:
        raise ValueError(
            f"no registered language adapter matched {root}. Adapters: "
            f"{', '.join(available())}. This is reported rather than returning an "
            f"empty graph, which would render as a codebase with no structure."
        )
    return Detection(best, scores[best.name], scores)
