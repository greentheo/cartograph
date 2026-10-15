# cartograph

**See a codebase well enough to supervise the agents working in it.**

A visual map of *any* repository, built from the source rather than from a
model's impression of it, with a hierarchy of coding agents attached at every
level.

Nothing about a mapped project is baked in. The language comes from an adapter,
everything else from an optional `cartograph.toml` in the repo being mapped.

**[`docs/SPEC.md`](docs/SPEC.md)** — the problem, the decisions and their
rejected alternatives, what is deliberately unbuilt and what unblocks it, and
the open questions.

Three organs:

1. **Crawler** — walks the tree and extracts what the code actually says:
   modules, definitions, imports, resolved call edges. Every element carries a
   `file:line`.
2. **Web app** — renders it as a navigable architecture. Blocks, arrows, drill-down
   from concept to module to symbol to source.
3. **Agent** — headless Claude Code running in the crawled worktree, answering
   questions against the graph and turning diagram edits into reviewable specs.

---

## The one design rule

**A map is a measurement, and the failure mode of a measurement is not an error
— it is a plausible wrong value.**

A diagram suppresses doubt better than a number does. If you are going to act on
this map, it has to be able to be wrong in a way you can notice. So:

| | rendered | means |
|---|---|---|
| **Fact** | solid, green | derived from the AST. Click it and you get the `file:line` that produced it. Change the source and it changes. |
| **Inference** | dashed, violet | a grouping or a name proposed by an algorithm or a model. Useful. Not evidence. |

The asymmetry that matters: **the boxes are a story, the arrows are evidence.**
Which modules share a block is a judgement. Whether module A calls module B is
a fact with 93 call sites behind it, each one openable.

**And absence is never silently clean.** Python has `getattr`, dynamic dispatch
and string-keyed registries; a static crawler will always miss edges. Coverage
is on screen at all times, so an absent arrow reads as *"static analysis did not
find it"* — never as *"it does not happen."*

---

## The agent hierarchy

A roster is not five prompts. It is a **separation-of-duties graph**, and the
duties are the point: the value of a reviewer is not that someone reads the
diff, it is that the reader is *not the author*.

Each seat declares what it holds, what is **explicitly withheld**, who it
escalates to, and the contract its answers must satisfy. The default roster is a
worked example — `cartograph.toml` replaces it.

| seat | may | must not | → |
|---|---|---|---|
| `socrates` | question | build, review, merge, deploy, decide | foreman |
| `research-assistant` | question, investigate | merge, deploy, decide | foreman |
| `engineer` | investigate, build | review, merge, decide | reviewer |
| `reviewer` | investigate, review | build, merge, deploy, decide | foreman |
| `foreman` | investigate, review, merge, decide | build, deploy | operator |
| `operator` | everything — the human | — | ends here |

**The capability model refuses at the API, before the model sees the task.**
Asking as `engineer` with `capability=merge` returns 403 naming who does hold
it; as `foreman` it returns 200. A seat that can talk itself into a capability
does not have a capability model, it has a suggestion.

`must_not` outranks `may`, an unknown capability is refused at construction, and
the escalation graph must terminate — a cycle means no seat ends the argument.

### The invariant no single agent can check about itself

> **The seat that raises a problem must not be the seat that resolves it.**

If it is, that seat is the only one in the system that can never be caught
getting it wrong — and a careful agent and a careless one are indistinguishable
from inside the same seat. `POST /api/separation` takes a list of
`(role, capability, subject)` and flags any seat that both raised and resolved
the same subject.

**"Not granted" and "explicitly withheld" are different claims** and render
differently. Collapsing them showed the human operator as "must not build",
which is not what the roster says — a policy display that turns silence into
prohibition is asserting something nobody wrote.

---

## Configuration — `cartograph.toml`

Optional. Absent means defaults; a tool that demands configuration before it
shows you anything does not get used.

It sets the language, the excluded directories, the view granularity, the
roster, and named channels. **Declaring any role replaces the roster entirely** —
a partial merge would let you believe you had removed a capability that a
default quietly restored. And a config that fails to parse **raises** rather
than falling back, for the same reason.

`GET /api/project` returns the config, the detected language, the roster in
force, and a copy-pasteable example.

---

## Languages

One `LanguageAdapter` per language; Python is the reference implementation.
Adding TypeScript means writing one class, not editing the crawler.

The seam is drawn where it is honest: above it is reasoning over a dependency
graph — provenance, coverage, clustering, roles, intents — which has nothing to
do with syntax. Below it is finding a module and recognising an import, which is
irreducibly per-language.

**The map says what it cannot see.** Detection reports the share of source files
the chosen adapter does *not* cover, derived from the adapter's own coverage
rather than from the scores of other registered adapters — the first version
compared registered adapters, so with only Python registered a repo that was a
third TypeScript reported as unmixed. The tool could only notice languages it
could already read, which is exactly backwards.

---

## What it found on its first real run

Pointed at a 620-module options-trading codebase:

```
620 files parsed, 0 failed, 1.2s
```

**`CONC_GRID = [2, 3, 5, 8, 12]`** — defined at `nested_walkforward.py:128`,
**read at zero sites**, while the value it names is hardcoded elsewhere. This
was already known from a human reading the source; the crawler found it from a
cold start in about a second. That is the known-answer check this tool had to
pass before any of its other claims were worth reading.

It also surfaced, unprompted, in the same file: `TOP_K`, `STRUCT_GRID`,
`BUDGETS` — and `SPLIT = 0.6` in `phase2_score.py`, and `EXIT_CHECK_SECS = 300`
in `core/operations.py`, whose own module docstring says the exit monitor runs
"every `EXIT_CHECK_SECS`".

---

## The wiring view

The highest-value organ, and the reason this exists rather than a diagramming tool.

The question that costs real money is not *"what calls what"* — it is **"is this
parameter actually connected to anything?"** A knob that looks like a control and
is not one produces a *plausible result*, never an error, which is why testing
the output never catches it:

- a grid constant defined once and read never, beside a hardcoded value — **a pin
  indistinguishable from a search**
- a CLI flag you can pass that changes nothing, and does not error
- a config key written before it is read, so **the file describing a run cannot
  describe what the run used**

Every verdict is a countable property of the source with `file:line` on both
sides. Not an opinion.

| verdict | meaning |
|---|---|
| `wired` | read at ≥1 site |
| `dead` | defined, read nowhere in the tree |
| `write_only` | written, never read |
| `mutated_before_read` | both written and read — check the order |

The analyser is deliberately conservative. It says `unknown` rather than
guessing, because **a report that cries wolf gets muted, and a muted control is
the thing this is trying to prevent.**

---

## Diagram edits become specs, not code

Draw a block, draw an arrow, and you get an **intent** — prose you read and
amend before an agent writes a line. Nothing is written to your repository by
drawing.

Every intent that introduces something which *runs* asks, in writing:

- What **closes** what this opens? Name the code path, not the intention.
- What **records** that it happened — before or after the side effect?
- If it raises *after* the side effect, what un-writes the record?

Those questions are generated mechanically rather than waiting for someone to
think of them, because the expensive miss is never "how do I build it" — it is
**what does it leave behind.**

---

## Run it

```bash
pip install fastapi uvicorn
python3 -m uvicorn cartograph.server:app --port 8899
open http://127.0.0.1:8899
```

Point the root box at any Python repo or worktree.

```bash
python3 -m pytest tests/ -q          # 52 tests
```

### Controls

- **gran** — how coarsely blocks are drawn. Higher is more, smaller blocks.
- **tests** — include test modules in the architecture. Off by default: on the
  codebase above, tests are 400+ of 620 modules, so including them makes the top
  level a map of the test suite rather than of the system.

---

## Status

Prototype. Working: the crawler, the wiring analyser, the cluster view with
drill-down, source-at-citation, the agent panel, intent generation.

Known and not hidden:

- **Call resolution is ~12% of candidate cross-module calls** on a large
  codebase. Every one of the remainder is counted and displayed. Resolution
  improves with type inference; the number will not be quietly rounded up.
- **Clustering is one flat level.** Genuine hierarchy — blocks inside blocks —
  is the next thing worth building.
- **Flow views are not built yet, deliberately.** Channels are declarable in
  `cartograph.toml` and nothing renders them. Building them against one
  codebase's three channels is how a general tool quietly becomes a bespoke one;
  they wait until the abstraction settles.
- **One language adapter ships.** The seam is real and tested; TypeScript, Go
  and Rust are unwritten.

## Tests

The suite is mutation-tested, not just run. Three deliberate breaks — draw an
edge that was not resolved, count a missed call as resolved, strip evidence off
a fact — were each confirmed to turn it red before the baseline was trusted.

The single check the whole tool stands on:

> **delete a real call from the source and the edge must disappear.**

A graph that survives that deletion unchanged is decoration, and nothing built
on it means anything.
