"""A diagram edit becomes a reviewable spec, not code.

You draw a box and an arrow; this turns it into prose you can read, argue with,
and hand to an agent. Nothing here writes to the repository.

The design decision worth defending: the intent carries OPEN QUESTIONS, and it
generates them mechanically rather than waiting for someone to think of them.
The question that keeps costing this codebase money is not "how do I build it"
but "what does it leave behind" — a Phase B order that filled into no local
state, with no stop, no expiry close and no reconciliation, because the design
was reviewed twice by people asking whether it would work rather than what it
would strand. So every intent that introduces something which runs asks, in
writing, what closes it, what records it, and what happens when it fails.

An unanswered open question is not a blocker. It is a visible blank on the spec,
which is the difference between a risk someone declined and a risk nobody saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .model import CodeGraph


@dataclass
class Intent:
    kind: str
    name: str
    summary: str
    target_path: Optional[str] = None
    called_by: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    cluster: Optional[str] = None
    purpose: str = ""
    open_questions: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["prompt"] = self.to_prompt()
        return d

    def to_prompt(self) -> str:
        """The handoff an agent receives. Plain prose, because you review it."""
        out = [f"# Intent: {self.kind} — {self.name}", "", self.summary, ""]
        if self.purpose:
            out += ["## Purpose", self.purpose, ""]
        if self.target_path:
            out += [f"**Suggested file:** `{self.target_path}`", ""]
        if self.called_by:
            out += ["## Must be called by", *[f"- `{c}`" for c in self.called_by], ""]
        if self.calls:
            out += ["## Must call", *[f"- `{c}`" for c in self.calls], ""]
        if self.context:
            out += ["## Existing code this touches", *[f"- {c}" for c in self.context], ""]
        if self.warnings:
            out += ["## Warnings", *[f"- **{w}**" for w in self.warnings], ""]
        if self.open_questions:
            out += [
                "## Open questions — answer these IN THE SPEC before writing code",
                "",
                "An unanswered question here is a blank on the design, not a blocker.",
                "\"We thought about it\" and \"we'll handle it later\" are the two answers",
                "this section exists to reject.",
                "",
                *[f"- [ ] {q}" for q in self.open_questions],
                "",
            ]
        out += ["## Acceptance",
                "- A test that FAILS before the change and passes after it.",
                "- For any guard added: the input that makes it fire, exercised in a test.",
                "- State what would make this change wrong, and show that check failing first.",
                ""]
        return "\n".join(out)


# Questions that are cheap to ask and expensive to skip.
_LIFECYCLE_QUESTIONS = [
    "What CLOSES or completes whatever this opens? Name the code path, not the intention.",
    "What RECORDS that it happened, and is that record written before or after the side effect?",
    "If this raises AFTER its side effect, what un-writes the record — and should it?",
    "What reads this output, and does an absent value there read as zero, as missing, or as valid?",
]

_MEASUREMENT_QUESTIONS = [
    "What would this measurement report if the thing it measures were DELETED? If the answer is "
    "'the same', it cannot fail and is not a measurement.",
    "What is the denominator, and does it come from a source independent of the numerator?",
    "What value would make you act differently? If none, this is decoration.",
]


def intent_from_edit(graph: CodeGraph, kind: str, name: str, *,
                     called_by: Optional[list[str]] = None,
                     calls: Optional[list[str]] = None,
                     cluster: Optional[str] = None,
                     purpose: str = "") -> Intent:
    called_by = called_by or []
    calls = calls or []

    if kind == "add_module":
        summary = (f"Create a new module `{name}`"
                   + (f", grouped under {cluster}" if cluster else "")
                   + ". It does not exist yet.")
        target = f"{name.replace('.', '/')}.py"
    elif kind == "add_edge":
        summary = f"Introduce a dependency: {', '.join(called_by) or '?'} -> `{name}`."
        target = None
    elif kind == "add_symbol":
        summary = f"Add `{name}` to an existing module."
        target = None
    else:
        summary = f"{kind}: {name}"
        target = None

    intent = Intent(kind=kind, name=name, summary=summary, target_path=target,
                    called_by=called_by, calls=calls, cluster=cluster, purpose=purpose)

    # Ground the spec in what is already there, with citations.
    for ref in [*called_by, *calls]:
        node = graph.node(ref)
        if node is None:
            intent.warnings.append(
                f"`{ref}` is not in the crawled graph — it may be a typo, or it may be "
                f"reached only dynamically, in which case static analysis cannot see it."
            )
            continue
        where = f" ({node.path})" if node.path else ""
        intent.context.append(f"`{ref}`{where} — currently "
                              f"{len(graph.out_edges(ref))} out-edges, "
                              f"{len(graph.in_edges(ref))} in-edges")

    intent.open_questions.extend(_LIFECYCLE_QUESTIONS)
    if any(w in f"{name} {purpose}".lower()
           for w in ("metric", "score", "ratio", "measure", "report", "rate", "efficiency")):
        intent.open_questions.extend(_MEASUREMENT_QUESTIONS)

    # A new module nobody calls is the commonest thing to draw and forget.
    if kind == "add_module" and not called_by:
        intent.warnings.append(
            "No caller was specified. A module nothing imports is dead on arrival and "
            "the test suite will still pass — name its caller before building it."
        )

    return intent
