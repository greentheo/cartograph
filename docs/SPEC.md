# cartograph — spec

*Living document. Started 2026-09-18. `README.md` says what is built; `CLAUDE.md`
says how to work here; this says what we are trying to make and why, what has
been decided, and what is still open.*

---

## 1. The problem

In the operator's framing, because the framing is the requirement:

> The codebase is too complex to edit manually, and I don't want to fully trust
> Claude Code to manage all of the code. I need to see at least at a high or
> medium level what's going on, be able to inspect it, and then work at a lower
> level — but using Claude Code to do the actual coding.

And, added second, which is the part that changed the design:

> **Claude also can't quite hold it in memory either.** And most importantly,
> **the goals and objectives and wiring is what seems to trip us up** —
> forgetting which parameters are hardwired and which interact. We also don't
> have a good idea about **pipelines and data flows for specific deliverables and
> output channels.**

Three claims, each with a different consequence:

1. **A human cannot hold a large system in their head.** → a visual map.
2. **Neither can the agent.** → the map is *context for agents*, not only a UI.
   Everything is JSON-serialisable with citations for exactly this reason.
3. **Wiring and objectives are where it actually goes wrong.** → the highest-value
   view is not "what calls what". It is **"is this parameter connected to
   anything, and what was it supposed to achieve?"**

### The thesis: who is good at what

The operator's framing, and the reason this is not just another diagram tool:

> Visual IDEs are basically useless for actual programming, and pure agentic
> coding is **too linear** — it doesn't take advantage of the human's visual
> cortex, which is more powerful than anything else. **Humans don't need to waste
> time trying to grok specific code, but code PATTERNS are very valuable to
> grok.** Bots are great contained code writers and increasingly good at
> abstraction, but still lack common sense.

A division of labour, and every design decision here falls out of it:

| | good at | so |
|---|---|---|
| **human** | seeing **patterns** at a glance; common sense | give them shape, repetition, asymmetry, anomaly — *not code* |
| **agent** | writing contained code; abstraction | let it write; keep it off judgement calls |
| **neither** | holding a large system in memory | that is the map's actual job |

Three consequences that constrain the build:

1. **Optimise the visual layer for PATTERN RECOGNITION, not code comprehension.**
   Rendering code on a canvas is what makes visual IDEs useless. What earns the
   screen is what a glance can catch and a file cannot: a block with one caller,
   a knob wired to nothing, a cluster that talks to everything, twelve things
   shaped alike and one that is not. **The wiring view is already this** — "dead
   constant" is a pattern, not a line of code — which is why it landed hardest.
2. **cartograph is not an editing surface.** VS Code stays the editor and Claude
   Code stays the author. Competing with either is the failure mode.
3. **Linearity is the defect being fixed.** An agent conversation is a line; a
   system is not. The map is where you see the whole at once — that is the
   capability neither a terminal nor an editor provides.

### What the tool is for

**Making a system legible enough to supervise.** The output is the operator's
judgement, not code. Everything else follows from that: if you are going to act
on this map, it must be able to be wrong *in a way you notice*.

### Who it is for

**Open source, self-hosted, run locally by one person and their agents.** Not a
SaaS; nothing phones home; source never leaves the machine. Other teams clone it,
point it at *their* repos, and declare *their* roster.

That has a consequence the first version did not have: **cartograph launches and
supervises Claude Code sessions in roles.** It is not only a viewer — it spins up
seats, points them at a local repo, and is the console they report to. The
roster, the action log and the launcher are therefore core, not optional.

Because it is open source and self-run, the seams must be genuinely extensible
rather than merely honest: a stranger must be able to add a language adapter or a
roster without editing the core.

### What it is not for

Replacing the editor. Claude Code still writes the code — cartograph decides
*what* gets written, by whom, and shows you the result.

---

## 2. The design rule

**A map is a measurement, and the failure mode of a measurement here is a
plausible wrong value, not an error.**

A diagram suppresses doubt better than a number does — which makes an
architecture diagram the most dangerous possible home for a confident wrong
claim. So:

| | rendered | means |
|---|---|---|
| **fact** | solid, green | from the source. Carries `file:line`. Refused at construction without evidence. Changes when the source changes. |
| **inference** | dashed, violet | a grouping, a name, a summary. Useful. Never evidence. |

Three corollaries, all enforced in code:

- **The boxes are a story; the arrows are evidence.** Which modules share a block
  is a judgement. Whether A calls B is a fact with N openable call sites.
- **Absence is never silently clean.** An unresolved reference is *counted*, never
  guessed into a plausible arrow. An absent edge means "static analysis did not
  find it", never "it does not happen".
- **Impossible cases stay out of denominators.** Builtins and self-calls can never
  be cross-module edges.

**The check the whole tool stands on:** delete a real call from the source and
the edge must disappear. A graph that survives that unchanged is decoration.

---

## 3. Decisions

Made with the operator 2026-09-18. Alternatives recorded because a decision
without its rejected options is indistinguishable from an assumption.

| # | decision | rejected | why |
|---|---|---|---|
| D1 | **Top-level blocks are semantic clusters, AST-backed** | runtime entry points · packages/directories · one module per block | Directories may not reflect coupling; per-module is unreadable at 600 modules. Clusters match how people talk about a system — at the cost of the grouping being inference, which is why it renders dashed. |
| D2 | **Hybrid provenance, always visible** | static-only · agent-first | Static-only is unreadable (blocks named after code, not concepts). Agent-first reads beautifully and is unfalsifiable. Hybrid keeps readability without letting a story wear a fact's clothes. |
| D3 | **A diagram edit becomes a spec an agent implements** | direct codegen · read-only v1 | Codegen from diagrams only ever produces skeletons. Read-only defers the hard problem. A reviewable intent keeps the human in the loop where the judgement is. |
| D4 | **Agent layer is headless `claude -p` per request** | live session messaging · Agent SDK in-process · file issues to a tracker | Simple, stateless, real file access. **Narrowed by D10** — headless stays the path for one-shot questions; sustained work runs in live sessions. |
| D5 | **General-purpose product, not a tool for one repo** | — | Added 2026-09-18 after the first version had a repo path in the UI and one codebase's defect history in the analyser defaults. That is how every internal tool dies: it encodes the first example as the general case. |
| D6 | **A roster is a separation-of-duties graph, not a set of prompts** | prompts-with-personas | A role model that cannot refuse anything renames agents without changing what any of them may conclude. Capability checks run at the API, before the model sees the task. |
| D7 | **Open source, self-hosted; it STEERS agents it did not launch** | hosted product · single-operator tool · *cartograph spawns sessions* | Others run it locally against their own repos. **CORRECTED 2026-09-18, same day**: the first draft said cartograph *launches* sessions. It does not. **A human launches Claude Code in a terminal and authorises cartograph to steer it**, the way Claude Code desktop does. Recorded as a correction rather than rewritten, because a spawner and a steerer have completely different blast radii and the difference must stay visible. |
| D8 | **Objectives are ONE model at three altitudes** | system-objectives only · per-change intent only · per-parameter purpose only | The operator's answer: they are the same missing layer — *declared intent bound to code*. System objective ("underwriting, not alpha"), the objective of one change, and what a single parameter is for, are the same edge type at different scopes. Closes Q2. |
| D9 | **The diagram is where work starts** | navigation-first, authoring as a side door | Primary loop is draw → intent → agent → branch → PR → status back on the diagram. The round trip is the product, not a convenience. Raises the bar on the map: you cannot author from a picture you do not trust. |
| D10 | **Drive real live sessions** | headless-only · model the roster without invoking | cartograph becomes the console for a running roster, addressing live sessions and preserving their accumulated context. Couples it to sessions being alive, which is the cost being accepted. |
| D11 | **One repo at a time, switchable** | portfolio / cross-repo view | "Like any good IDE." Containment at the repo level keeps the data model simple. An objective spanning two repos is out of scope until someone has one and it hurts. |
| D12 | **cartograph runs its own dedicated agent** | only user-invoked agents | The tool needs a seat of its own — it mediates questions, and it is how the map gets CORRECTED: an agent that finds an edge static analysis missed tells the cartograph agent, which records it against the graph. Distinct from roster agents, which do the work. |
| D13 | **Target scale: under 50k files** | large monorepos | Sets the architecture. The current design re-crawls in-memory per request — fine at 620 files (1.2s), **not fine at 50k**, so incremental indexing is a real requirement, not a nicety. Above 50k is a different program and explicitly not this one. |

---

## 4. The organs

```
  source tree
      │
      ▼
  ┌──────────────┐   facts only, every element cited
  │  CRAWLER     │   ast_index.py · one LanguageAdapter per language
  └──────┬───────┘
         │  CodeGraph (Node/Edge/Provenance/Coverage)
         ├──────────────────────┬─────────────────────┐
         ▼                      ▼                     ▼
  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
  │  CLUSTER     │      │  WIRING      │      │  AGENT       │
  │  inference   │      │  facts       │      │  cited answers│
  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
         └──────────────┬──────┴─────────────────────┘
                        ▼
                 ┌──────────────┐
                 │  WEB APP     │  map · wiring · roster · ask · propose
                 └──────────────┘
                        │
                        ▼
                   INTENT  →  reviewed as prose  →  agent implements
```

**Wiring is the differentiator**, and the reason this exists rather than a
diagramming tool. A knob that looks like a control and is not one produces a
*plausible result*, never an error — which is why testing the output never
catches it. Verdicts are countable properties of the source with `file:line` on
both sides: `wired` · `dead` · `write_only` · `mutated_before_read`.

---

## 5. Status

**Built:** crawler · wiring · clustering · language seam · project config ·
roster with enforced capabilities · headless agent with citation contract ·
intent generator · web app (map, drill-down, source-at-citation, wiring, roster,
ask, propose).

**Deliberately not built — each with the condition that unblocks it:**

| not built | unblocked when |
|---|---|
| **Flow / channel views** | the general abstraction settles. Building them against one codebase's three channels is how a general tool becomes a bespoke one. |
| **Action log** | *nothing — this is next.* It is the missing producer for `check_separation`, without which the roster is declarative and no agent can be orchestrated. |
| **Adapters beyond Python** | someone needs a second language. The seam is real and tested; the work is one class. |
| **Real hierarchy** (blocks inside blocks) | the top level is judged legible at one level first. |
| **Running / debugging code from the map** | a human needs it. Confirmed thinking-out-loud, not a requirement: **the agents run and debug it**, and they already can. Human-facing debugging waits for a human who wants it. |
| **Incremental indexing** | the corpus approaches ~5k files, or a re-crawl exceeds a few seconds. Required before D13's 50k ceiling means anything. |
| **Agent-proposed block names** | the naming contract is written — a pretty name must never imply a verified concept. |

---

## 6. Open questions

Genuinely open. Answering any of these changes what gets built.

**Q1 — Is coupling the right basis for a block?**
Modularity clusters group by *who talks to whom*. The operator asked for blocks
that "represent functionality or core pieces of code for the area of interest" —
which is purpose, not coupling. They coincide often and not always. Does the top
level need a different primitive: entry-point reachability, declared domains, or
agent-proposed grouping checked against coupling?

**Q3 — Should there be a third provenance kind, `observed`?**
Static resolution sits at ~27% of candidate cross-module calls on a large
codebase. Runtime tracing would close much of the gap — but a runtime-observed
edge is neither an AST fact nor an inference. It is *evidence from one execution*,
which is a different and weaker claim than "this is in the source". A third kind
with its own rendering may be more honest than folding it into either.

**Q4 — What is the unit of agent work?**
Today: one question, one invocation, no memory. A roster implies tasks,
handoffs, and a record. Is the primitive an intent, a tracker issue, or something
native? This blocks orchestration and is entangled with the action log.

**Q5 — How does the map stay correct while you edit?**
The cache reports its age and flags staleness, but nothing invalidates on file
change. A map twenty minutes old while you are editing is the
artifact-has-a-build-date defect with a fresh coat of paint.

**Q6 — What is a channel, generally?**
Probably `entry points → transforms → sinks`, i.e. reachability plus I/O
detection. But "the deliverable" may need declared semantics that no static
analysis can supply. Unresolved, and Q6 gates the flow views.

---

## 6b. The knowledge layer — objectives, and doubt that propagates

**Decided in principle (D8), unbuilt.** This is the largest gap between what was
asked for and what exists, and it is one layer, not three features.

An **objective** is declared intent bound to code, at whatever scope:

```
  system     "this is underwriting, not alpha — score on loss ratio, not Sortino"
  change     "#895: three stuck positions close"
  parameter  "conc = how many concurrent positions per system; sane 1-12"
```

All three answer the same question — *what is this for, and what would tell us it
stopped serving its purpose?* — so they are one node type with a scope, not three
subsystems.

### Borrow the typed-edge design, do not reinvent it

A neighbouring codebase (`research_studies/tree.py`) already solves the hard half,
and its reasoning transfers exactly:

> A citation graph records what cited what; it cannot answer **"this turned out to
> be false — what else is now in doubt?"** That is why edges are typed, and why
> the *instrument* is a first-class node rather than a footnote: repairing one
> marks every result taken with the broken version stale, automatically.
>
> Invalidation marks descendants **`suspect`, never `disproven`** — a suspect
> result may well survive re-verification, and forcing that judgement to be
> recorded is the point.

That is precisely what an objectives layer needs. Two properties to carry over:

- **Typed edges, so doubt propagates.** `serves`, `measured-by`, `moved-by`,
  `assumes`, `uses-method`. When a parameter turns out dead, every objective it
  was supposed to move goes `suspect` — which is the real version of the
  `CONC_GRID` finding. Today the tool says "this constant is dead"; it should say
  *"and this objective now has no live knob."*
- **The instrument is a node.** Our own provenance model already says a claim
  carries its method. Making the method a node means fixing a crawler defect
  marks every map-derived conclusion taken with the old version stale — rather
  than leaving five hand-found defects as prose in a log.

### Where it must NOT go

Do not copy the neighbouring implementation. Its nodes are research findings on
one project; ours are objectives bound to code in any repo. **Borrow the idea,
write the model here**, or cartograph acquires one codebase's schema — which is
exactly D5's failure.

---

## 7. The defect log

Every defect found in cartograph so far has been **the same shape: a display
asserting something nobody wrote.** Recorded because the pattern predicts the
next one.

1. A constant reported **dead** while another module read it as `pkg.CONST`.
   *A false "dead" is the direction that discredits the whole report.*
2. Coverage divided by **every** call site including `len()` — read 11% when the
   honest figure over eligible calls was 27%.
3. `is_mixed` compared only **registered** adapters, so the tool could only
   notice languages it could already read.
4. The roster UI rendered "not granted" identically to "explicitly withheld",
   showing the human operator as forbidden from building.
5. **`check_separation` shipped with no producer** — a control that cannot fire,
   inside the module whose docstring is about controls that cannot fire.

All five were found by **checking output by hand against a real corpus**, none by
a unit test. That is the method, not an anecdote: point it at a large codebase
with known answers and read what it says.

**Test corpus, not cartograph itself.** cartograph is ~1,500 lines and maps to 13
modules in blocks of 5/4/2/1/1 — a shape that teaches nobody anything. A
known-answer check needs an external oracle.

---

## 8. Blast radius — the trust model

*Proposed, not decided. This is what "open to suggestions" got; argue with it.*

A tool that steers agents with write access needs a story before it is public.
**D7's correction does most of the work**: cartograph does not spawn anything, so
its reach is bounded by sessions a human already started and explicitly
authorised.

Six properties, roughly in order of how much they matter:

1. **cartograph never launches an agent.** A human starts Claude Code in a
   terminal and authorises steering, the way Claude Code desktop does.
   Authorisation is per-session, revocable, and should be time-boxed.
2. **cartograph is read-only on the mapped repo.** It crawls. Its own state goes
   in its own directory and never into the repository it is looking at. Anything
   that writes to your code writes through an agent you started.
3. **A capability cartograph holds is NOT a permission the session holds.** This
   is the load-bearing one. The roster is a *policy* layer on top of Claude
   Code's own permission system, never a substitute for it — and cartograph must
   have no way to raise a session's permissions. If a session was denied
   something, cartograph asking on its behalf must not change that. Laundering a
   denied permission through a second actor is the specific attack, and it is
   easy to build by accident.
4. **The roster narrows; it never widens.** A seat can be refused a capability it
   would otherwise have. No configuration may grant a session more than the human
   gave it.
5. **Every steer is recorded** in the action log with role, capability, subject
   and time — so it is auditable afterwards, not merely gated beforehand.
6. **Fail closed.** An unparseable `cartograph.toml` already raises rather than
   falling back to defaults, for exactly this reason: a policy that fails open
   restores everything you thought you removed, silently.

**What this does not cover, and I would rather say so:** a malicious
`cartograph.toml` in a cloned repo, prompt injection reaching a steered session
through mapped source, and the fact that an authorised session can do anything
its own permissions allow regardless of what the roster says. Those need separate
answers.
