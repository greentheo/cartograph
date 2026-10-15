"""The agent layer: headless Claude Code, run in the target worktree.

Deliberately thin. The agent is not asked to describe the architecture from
scratch — it is handed the graph (facts, with citations) and asked to answer a
question against it. That ordering matters: a model asked "what is the
architecture here" will produce a confident, readable, unfalsifiable story. A
model handed 200 real edges and asked "which of these carries the P&L sign" can
be checked against the source, and so can be wrong in a way anyone notices.

Every answer is required to come back with citations. An answer with no
`file:line` is treated as unsupported and labelled as such in the UI rather than
being quietly rendered next to answers that do have evidence.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class AgentUnavailable(RuntimeError):
    """The `claude` CLI is not on PATH. Said plainly rather than degraded silently."""


@dataclass
class AgentAnswer:
    question: str
    text: str
    role: Optional[str] = None
    citations: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None
    raw: Optional[str] = None

    @property
    def is_supported(self) -> bool:
        """An answer with no citation is an opinion. The UI must say so."""
        return bool(self.citations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "role": self.role,
            "text": self.text,
            "citations": self.citations,
            "node_ids": self.node_ids,
            "ok": self.ok,
            "error": self.error,
            "is_supported": self.is_supported,
        }


def claude_available() -> bool:
    return shutil.which("claude") is not None


ANSWER_CONTRACT = """\
You are answering a question about a codebase you are standing inside.

Reply with ONE JSON object and nothing else:

  {"text": "<your answer, prose, <= 200 words>",
   "citations": ["relative/path.py:LINE", ...],
   "node_ids": ["<dotted.module names you are talking about>", ...]}

Rules that matter:
- EVERY factual claim needs a citation you actually opened. Do not cite from memory.
- If you cannot find something, say so in `text` and return an empty `citations` list.
  An honest "not found" is worth more here than a plausible guess; a wrong answer
  with a citation-shaped string in it is the worst possible output.
- Prefer naming the exact function and line over describing the area.
"""


def _extract_json(raw: str) -> Optional[dict]:
    """Pull the JSON object out of a CLI response that may carry prose around it."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw
        raw = raw.lstrip("json").strip()
    start, depth = raw.find("{"), 0
    if start == -1:
        return None
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def ask(question: str, cwd: str | Path, *, context: str = "",
        timeout: int = 180, model: Optional[str] = None,
        role: Optional["Any"] = None) -> AgentAnswer:
    """Ask a question of the code in `cwd` using headless Claude Code.

    `context` is the graph excerpt — the blocks and edges currently on screen —
    so the agent answers about what the operator is looking at rather than
    re-deriving the whole repository on every question.

    `role` is a `roles.Role`. Its preamble goes FIRST, ahead of the answer
    contract, because a seat's withheld capabilities have to be established
    before the task is described — an agent told what to do and only afterwards
    what it may not do has already started planning around the task.
    """
    if not claude_available():
        raise AgentUnavailable(
            "the `claude` CLI is not on PATH; the agent panel cannot answer. "
            "This is reported rather than silently returning an empty answer."
        )

    role_name = getattr(role, "name", None)
    prompt = ""
    if role is not None:
        prompt += f"{role.preamble()}\n\n{'-' * 60}\n\n"
    prompt += f"{ANSWER_CONTRACT}\n"
    if context:
        prompt += f"\nWhat the operator is currently looking at:\n{context}\n"
    prompt += f"\nQuestion: {question}\n"

    cmd = ["claude", "-p", prompt]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return AgentAnswer(question=question, text="", ok=False, role=role_name,
                           error=f"agent timed out after {timeout}s")
    except OSError as exc:
        return AgentAnswer(question=question, text="", ok=False, role=role_name, error=str(exc))

    if proc.returncode != 0:
        return AgentAnswer(
            question=question, text="", ok=False, raw=proc.stdout, role=role_name,
            error=f"claude exited {proc.returncode}: {(proc.stderr or '').strip()[:400]}",
        )

    parsed = _extract_json(proc.stdout)
    if parsed is None:
        # Return the prose rather than pretending it parsed. A silent empty
        # answer is the failure mode this whole project exists to avoid.
        return AgentAnswer(
            question=question, text=proc.stdout.strip()[:2000], ok=True,
            raw=proc.stdout, role=role_name,
            error="response was not JSON; showing raw text and treating it as unsupported",
        )

    return AgentAnswer(
        question=question,
        role=role_name,
        text=str(parsed.get("text", "")).strip(),
        citations=[str(c) for c in parsed.get("citations", []) or []],
        node_ids=[str(n) for n in parsed.get("node_ids", []) or []],
        raw=proc.stdout,
    )


def graph_context(graph, focus: Optional[str] = None, limit: int = 60) -> str:
    """A compact text rendering of the current view, for the agent's prompt.

    Kept small on purpose: the agent has file access and can read anything it
    needs. What it cannot reconstruct cheaply is which blocks the operator is
    looking at right now, so that is what we send.
    """
    lines: list[str] = []
    if focus:
        node = graph.node(focus)
        if node is not None:
            lines.append(f"FOCUS: {node.label} ({node.id}, {node.kind})")
            if node.path:
                lines.append(f"  file: {node.path}")
            members = node.meta.get("members")
            if members:
                lines.append(f"  contains: {', '.join(members[:25])}")

    mods = [n for n in graph.nodes_of("module")][:limit]
    if mods:
        lines.append("\nMODULES IN VIEW:")
        lines += [f"  {n.id}  ({n.path})" for n in mods]

    edges = [e for e in graph.edges if e.kind == "calls"][:limit]
    if edges:
        lines.append("\nCALL EDGES (from static analysis, each backed by file:line):")
        for e in edges:
            ev = e.provenance.evidence[0] if e.provenance.evidence else "?"
            lines.append(f"  {e.source} -> {e.target}  (x{e.weight}, e.g. {ev})")

    cov = graph.coverage
    lines.append(
        f"\nCOVERAGE: {cov.resolved_calls}/{cov.call_sites} call sites resolved "
        f"({cov.call_resolution_rate:.0%}). {cov.unresolved_calls} could not be "
        f"resolved statically, so an ABSENT edge does not prove the call does not happen."
    )
    return "\n".join(lines)
