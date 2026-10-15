# cartograph — instructions for a session working in this repo

**See a codebase well enough to supervise the agents working in it.**

This is a general-purpose product. It is not a tool for any particular
repository, and the fastest way to ruin it is to solve one codebase's problem in
the core and call it a feature.

**`docs/SPEC.md` is the living spec** — the problem in the operator's own
framing, the decisions and what they rejected, status with unblock conditions,
and the open questions. Read it before proposing direction. Update it when a
decision is made or a question closes; a spec that lags the code is worse than
none, because it reads as current.

## Working directory

`~/githubrepo/cartograph`. Nothing here depends on any other repo.

```bash
python3 -m pytest tests/ -q                                   # must be green
python3 -m uvicorn cartograph.server:app --port 8899           # then open it
```

## The one rule everything else follows from

**A map is a measurement, and the failure mode of a measurement here is a
plausible wrong value, not an error.** A diagram suppresses doubt better than a
number does. So:

- **Facts and inferences are quarantined and rendered differently.**
  `Provenance(kind="ast")` carries a `file:line` and is *refused at construction
  without one*. `Provenance(kind="inferred")` is a grouping or a name.
- **The boxes are a story; the arrows are evidence.** Which modules share a block
  is a judgement. Whether A calls B is a fact with N openable call sites.
- **Absence is never silently clean.** An unresolved reference is COUNTED, never
  guessed into a plausible arrow. An absent edge means "static analysis did not
  find it", never "it does not happen" — and the UI must say so.
- **Impossible cases stay out of denominators.** Builtins and self-calls can
  never be cross-module edges; counting them understates coverage exactly as
  reliably as guessing overstates it.

## What this project keeps getting wrong, so check for it

Every defect found here so far has been the same shape: **a display asserting
something nobody wrote.** Four instances, all caught by checking output by hand
rather than by a test:

1. A constant reported dead while another module read it as `pkg.CONST`.
2. Coverage divided by every call site including `len()` — read 11% when the
   honest figure was 27%.
3. `is_mixed` compared only *registered* adapters, so the tool could only notice
   languages it could already read.
4. The roster UI rendered "not granted" identically to "explicitly withheld",
   showing the human operator as forbidden from building.

**And one worse: a control with no producer.** `RoleRegistry.check_separation`
was shipped with no caller that supplies real actions — a check that cannot fire,
inside the module about checks that cannot fire. Before adding any control, name
what feeds it.

## Testing

**Make it fail first.** A green suite is not evidence until you have watched it
go red. Mutate the thing under test — draw an edge that was not resolved, count a
missed call as resolved, strip evidence off a fact — and confirm red *before*
trusting green.

**Every detector test needs a control that must NOT fire.** A checker that flags
everything passes a permission test exactly as well as a correct one. The
false-positive direction is the worse one: it trains a reader to stop looking.

The single check this whole tool stands on:

> **Delete a real call from the source and the edge must disappear.**

A graph that survives that unchanged is decoration, and nothing built on it means
anything.

## Roles

`cartograph/roles.py` models a roster as a **separation-of-duties graph**, not a
set of prompts. A seat declares `may` / `must_not` / `escalates_to` /
`answers_with`, and capability checks run at the API **before the model sees the
task** — a seat that can talk itself into a capability has a suggestion, not a
capability model.

The invariant: **the seat that raises a problem must not be the seat that
resolves it.** Otherwise it is the one seat that can never be caught getting it
wrong.

This repo uses the **default roster** rather than declaring one in
`cartograph.toml`. Declaring any role *replaces* the roster entirely, so a copy
here would drift from `DEFAULT_ROSTER` silently — and a stale policy reads as
permissive, because nothing it fails to mention is forbidden.

## Architecture

```
cartograph/
  model.py              Node/Edge/Provenance/Coverage. Provenance is enforced here.
  crawler/ast_index.py  ground truth: modules, defs, cited import/call edges
  crawler/wiring.py     is this parameter connected to anything?
  crawler/cluster.py    modularity communities (INFERRED)
  languages.py          LanguageAdapter seam — one class per language
  config.py             cartograph.toml; nothing about a mapped repo is hardcoded
  roles.py              the roster and its invariants
  intent.py             a diagram edit becomes prose you review, never code
  agent/claude.py       headless Claude Code; answers must carry citations
  server.py             FastAPI
web/                    single page, Cytoscape from CDN, no build step
```

**Test corpus:** point it at a large real repository with known answers, not at
cartograph itself. cartograph is ~1,500 lines — small enough to hold in your
head, so mapping it proves nothing. A known-answer check needs an external
oracle.

## Not built, deliberately

- **Flow views / channels.** Declarable in `cartograph.toml`, rendered by
  nothing. Building them against one codebase's channels is how a general tool
  quietly becomes a bespoke one. They wait until the abstraction settles.
- **An action log.** The missing producer for `check_separation`. Until it
  exists, the roster is declarative and cartograph cannot orchestrate agents.
- **Any adapter but Python.** The seam is real and tested; the rest are unwritten.

## The task queue — GitHub Issues on `greentheo/cartograph`

```bash
gh issue list --state open
gh issue view 12
gh issue create --label "defect,corpus" --title "..." --body "..."
gh issue edit 12 --add-label in-progress
```

**The issue body IS the spec.** No separate spec file per issue — cross-cutting
work cites a `docs/SPEC.md` section instead (e.g. "see SPEC §6b").

Labels, and what each actually means:

| label | use it when |
|---|---|
| `defect` | **a display asserting something nobody wrote, or a control that cannot fire.** This project's recurring shape — see the defect log in `docs/SPEC.md §7`. |
| `corpus` | found by running the tool against a real codebase rather than by a test. Most real defects arrive this way; the label exists so that stays measurable. |
| `abstraction` | something bespoke is leaking into the core. D5's failure mode, and the fastest way to ruin this project. |
| `spec` | it changes a decision or closes an open question in `docs/SPEC.md`. |
| `adapter` | language adapter work. |
| `enhancement` | new capability. |
| `needs-human` | waiting on the operator. Keep it **open**. Not a shrug — use it when the operator specifically must act. |

- Branch per issue: `feature/{N}-short-description` or `fix/{N}-...`
- Commit `[#{N}] what changed and why`; PR references it with `Closes #{N}`.
- **File what you notice, do not fix it now.** A defect found while doing
  something else becomes an issue, not a detour — one logical change per branch.
- **A concern raised only in conversation is indistinguishable from no concern.**
  If it would change what the next reader does, it goes on the issue or in the
  spec, not only into chat. Sessions end; the board survives.

## Where knowledge goes

**`docs/SPEC.md` is the record** until the knowledge layer exists:

- a **decision** → the decisions table, **with the alternatives it rejected**. A
  decision without them is indistinguishable from an assumption, and the next
  reader cannot tell whether it was chosen or defaulted into.
- an **open question** → §6, phrased so that answering it changes what gets built.
  Close it by turning it into a decision, and say so in the commit.
- a **defect** → §7, the defect log. All of them so far are one shape and the
  pattern predicts the next one. Add the instance, not a rewording.
- **deferred work** → §5, and it must name **the condition that unblocks it**. A
  deferral with no condition is a todo that rots invisibly, because "deliberately
  not built" reads as a decision forever.

**Update the spec in the same commit as the change it describes.** A spec that
lags the code is worse than none, because it reads as current — which is the
exact defect this tool exists to catch, committed against ourselves.

### The knowledge layer is planned, not built (SPEC §6b, D8)

Objectives bound to code, with **typed edges so doubt propagates**. Until it
exists there is no node store here, so do not write to one or cite one.

A private sibling project solves the hard half, and its reasoning is the part
worth carrying over — typed edges, the subject of study as a first-class node,
and invalidation that marks descendants `suspect` rather than `disproven`.
**Borrow the idea; do not copy the implementation.** Its
schema is research findings on one project; ours is objectives bound to code in
any repo, and importing its schema is precisely the `abstraction` defect.

## Conventions

- Python 3.11+, type hints on public functions.
- No new dependency without a reason that survives being said out loud. The web
  layer has no build step on purpose.
- Commit messages say what changed and **what would have to be true for it to be
  wrong**.
- Do not add a capability, verdict, or provenance kind without a test that shows
  it refusing something.
