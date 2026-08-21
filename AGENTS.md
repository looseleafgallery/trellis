# Working with trellis

You are reading this because someone wants you to build or maintain a trellis
graph for them. This is the operating manual for doing that. Read `README.md`
for what the tool is; this file is about how to use it *with* a person.

## The one rule

**You transcribe the tool's objections. You do not generate them.**

A trellis graph is worth having only if it is honest, and the thing that makes
it honest is that it disagrees. That disagreement has to come from `trellis
doctor`, not from your judgment — because your judgment is agreeable, and a
bootstrap that ends with "here is your graph, looks good" has taught the user
nothing and will not be opened again.

So: you ask the questions, you write the YAML, and then you run `trellis
doctor` and report what it says. If it says nothing, say that too, and say what
it means:

```
$ trellis doctor
nothing looks wrong across 6 nodes.
that is either a good graph or a graph too small to disagree with - if you
just bootstrapped it, it is probably the second.
```

## The model in sixty seconds

Two node kinds, declared as YAML under a `graph/` directory.

**Work** — anything that can be finished. Nests via `parent`.

```yaml
id: agent.tool_exec
title: Tool execution stage
parent: agent
status: not_started
gates:
  start: agent.plan.done and contract.tool_schema.live
  finish: tools.streaming_results
provides: [tool-results]
```

**Contract** — an agreement *between* subsystems, and the single most valuable
thing in the model. A contract is `live` only when it is agreed **and**
everything implementing it is done.

```yaml
id: contract.tool_schema
kind: contract
status: agreed
version: 2
satisfied_by: [tools.registry, tools.sandbox]
```

Readiness (`blocked` / `ready` / `active` / `done` …) is **computed**. Never
declare it, never try to write it, never tell the user a node is blocked
without having run the tool.

## Grammar you will get wrong from memory

Copy from here rather than recalling.

**Statuses.** Work: `not_started`, `in_progress`, `done_unverified`, `done`,
`superseded`, `abandoned`. Contract: `draft`, `proposed`, `agreed`, `frozen`.

**There is no `depends_on:`.** Edges come from the references inside gate
expressions. Writing a dependency list does nothing; the loader rejects the
unknown field.

**Gate expressions** are a small subset of Python. Facts, not comparisons to
strings:

```
agent.plan.done and contract.tool_schema.live and contract.tool_schema.version >= 2
```

Right: `a.done` · `c.live` · `has(tools.registry, "tool-discovery")` ·
`all_done(a, b)` · `any_done(a, b)` · `count_done(a, b) >= 2` · `at_least(2, a, b, c)`

Wrong: `a.status == "done"` · `a == done` · `depends_on: [a]` · `a.ready`

**Facts on every node:** `done`, `complete`, `active`, `abandoned`,
`superseded`, `dead`, `provides`, `children_done`, `progress`, `leaf_done`,
`leaf_total`. Contracts add `live`, `agreed`, `frozen`, `version`.

**Gate names are open.** `start` drives readiness and `finish` is checked
against a completion claim; any other name (`review_passed`, `security_signoff`)
is evaluated and reported without gating anything.

**Published facts** are how a subsystem exposes itself. Gate on these, never on
another subsystem's internals:

```yaml
id: tools
publishes:
  streaming_results: has(tools.streaming, "streaming-results")
```

**Published facts are the *external* interface.** Gate on them from *other*
subsystems. Inside a subsystem, a sibling references its sibling directly —
gating on your own parent's published fact closes a cycle, because the parent
already depends on its children through rollup.

```yaml
# from outside `tools`:      gates: {start: tools.streaming_results}   correct
# from a sibling inside it:  gates: {start: tools.streaming.done}      correct
# from a sibling inside it:  gates: {start: tools.streaming_results}   cycles
```

**Provenance** records why you believe an edge:

```yaml
evidence:
  agent.reflect: {how: verified, at: 2026-08-20}
  contract.stage_handoff: inferred
```

`how` is one of `verified` (checked against a system of record), `stated`
(someone told you), `inferred` (you worked it out), `assumed` (nobody said it).

**Evidence keys are node ids, even when the gate names a published fact.** The
gate below references `tools.streaming_results`; the evidence annotates `tools`,
the node that publishes it:

```yaml
id: agent.tool_exec
gates:
  finish: tools.streaming_results
evidence:
  tools: {how: verified, at: 2026-08-21}
```

## Bootstrapping a graph

### The interview is how you reach the person

Not a fallback for when there is no documentation. If a team has good prose
about their initiative, it is because **someone has been maintaining it for
weeks** — and that person is already carrying the graph in their head. The
documents are a written cache of what they know. Talking to them is fast and
high-confidence.

If a team has no such documents, they usually do not have that person either.
The interview then becomes archaeology across several people: slow, and it
produces `stated` rather than `verified`.

This is also why contracts come out first and nearly free. A contract is the
thing two people argued about, so it is the precise part of their memory.

### Take nodes from anywhere; take edges from a system of record

The single most useful rule here, and it is measured rather than asserted. On
the first real bootstrap, of 7 edges read out of prose and then checked against
the tracker, **2 were wrong.** Every edge taken from the tracker's `blocked_by`
was right.

> Take nodes from wherever you like. Take **edges from a system of record**.
> Interview for edges only where no system of record has them.

The failure mode is specific: prose interleaves edges, hints, and commentary in
one sentence. A cell reading *"G5, X3, and A4's held-status surfacing"* is two
edges and a third thing that is a *consequence*, not a dependency. An importer
cannot separate those, and a careful reader gets it mostly right — which is
worse than obviously wrong, because it looks finished.

**Extraction is faster to a complete-looking graph and slower to a true one.**
So if you extract from documents: extract, annotate honestly as `inferred`,
then reconcile against the system of record *before* anyone trusts a readiness
answer. Those three steps are the difference between the two outcomes.

### What they already have decides what to ask

Three starting conditions, needing the questions in near-opposite proportions.

**Blank page.** Interview for everything.

**A tracker and nothing else.** Most teams. The tracker has work nodes, and
verified edges for free in whatever `blocked_by` field it offers. What it
cannot have is **contracts** — no tracker models an agreement between two teams
as an object. Interview almost entirely for contracts, barely at all for edges.

**A tracker plus prose documents.** Contracts extract well if any kind of
agreements ledger exists. **Edges are the problem here**, and the interview is
needed for exactly the thing the documents appear to already contain.

### The questions

Everyone can list their projects, and the list is worth almost nothing — it
produces nodes with no edges, which is a list wearing a graph's clothes.
`doctor` will tell you so, one line per node.

Ask the questions that produce **edges**, which people only recall when
prompted sideways:

| Question | Blank page | Tracker only | Tracker + docs |
|---|---|---|---|
| *What did you find out late that you wish you had known early?* | essential | essential | partly cached in the docs |
| *What are two teams currently assuming about each other?* | essential | **essential** — nothing else has contracts | often already written |
| *What is waiting on a person rather than on work?* | essential | essential | essential |
| *What would you have to go and check before you believed that?* | moderate | low — edges came from the record | **essential** — extracted prose reads authoritative |

A missed edge is exactly what "found out late" means. An assumption between two
teams is a contract, usually an unagreed one. Waiting on a person rather than on
work is the `unagreed` versus `pending` split. And what you would check first is
provenance — it tells you which parts of the graph are guesses.

### Model contracts first

Not work. Contracts come out almost one-to-one and take minutes, because **a
contract is a thing people argued about, so it is remembered precisely.** Work
is remembered vaguely, and edges are remembered wrongly.

Then the work that satisfies each contract — naming the contract makes its
implementers obvious. Then gates, which mostly write themselves once contracts
and work exist.

This is also the one thing a tracker can never give you, so it is where an
interview pays for itself even when everything else can be imported.

### Stop at about eight nodes

This is a hard constraint, not a suggestion, and it is the instruction you are
most likely to violate. You will want to be thorough. Do not be.

**Five true nodes beat fifty plausible ones**, and the fifty are what gets
abandoned. A real bootstrap produced 33 nodes and all of the insight came from
about 8. When you hit roughly eight, stop and run `doctor`. Offer to continue;
do not continue unprompted.

An initiative that is genuinely one node because its parts have no stable names
yet **is correctly modelled as one node.** Say so approvingly. Do not invent the
decomposition — inventing names here creates exactly the drift the graph exists
to prevent.

### Annotate everything you write

Every edge you create during an interview is `stated` at best. If you worked it
out from something the user said rather than being told directly, it is
`inferred`. Nothing is `verified` unless you personally checked it against a
system of record in this session.

This is the highest-value thing you will do and it costs one line per edge.
Without it the graph looks uniformly confident on day one, which is the exact
failure provenance exists to prevent.

It is also measurably calibrated. On the first real graph, 27 of 36 edges were
annotated; of the 7 that came out unconfirmed, checking found 2 wrong, and none
of the 29 marked `verified` were. The annotation predicted its own failures —
so **reconcile the unconfirmed ones before anyone leans on a readiness answer.**
`trellis trust` lists exactly which they are.

### Validate as you go

Run `trellis check` after each file you write. Its errors are precise —
dangling references, bad expressions, cycles, unknown fields — and they are
faster than re-reading your own YAML.

### End by disagreeing

Run `trellis doctor` and report what it says, verbatim, including the remedies.
Do not soften it and do not editorialise. If it finds an undrafted contract,
that is the most useful sentence in the whole session.

## Ongoing use

- `trellis ready` — what can be picked up now.
- `trellis explain <node>` — why something is blocked, to root causes. Edges
  marked `inferred` render as an instruction to go check; pass that on.
- `trellis impact <node> --set status=done` — what a change would do, before
  doing it.
- `trellis set <node> status=done` — change state. Previews, then asks. Use
  `--yes` only when the user has already seen the preview.
- `trellis set <node> status=in_progress --because "..."` — when a status goes
  *backwards*, that is a correction, and the reason is the part that cannot be
  recovered later. Always pass `--because`, or answer the prompt. See below.
- `trellis trust` — what is stale, what churns, which edges were never
  confirmed.
- `trellis blocking <node>` — what it is holding up. Reports two numbers:
  what unlocks the moment it lands, and what is blocked downstream behind it.
  **Never quote the second as the first** — they answer different questions,
  and conflating them is how people misreport their own graph.
- `trellis blocking --all` — chokepoints, ranked.
- `trellis graph --around <node>` — a mermaid slice to paste where someone else
  can read it. Useful when the person you need agreement from does not have
  trellis.
- `trellis snapshot -m "why"` — freeze what the graph means now, for later.
  **Never quote a snapshot as current state.** It is frozen by definition; if
  someone asks how things are, run `state` or `doctor` instead. Say when a
  snapshot was taken whenever you cite one.
- `trellis drift` — has anything been changed around the tool since it last
  wrote? Worth running at the start of a session.
- `trellis doctor` — run this more often than you think. Findings come ranked;
  the first one is the one to act on.
- `trellis review` — the same findings as an interactive session, one at a
  time. **This one is for the user, not for you**: it is where a person makes
  the calls. Suggest it when `doctor` returns more than a handful of findings.

If a finding is true and will stay true — a spike with no relationships, say —
answer it for good on the node rather than re-reading it every run:

```yaml
acknowledge: [inert_node]
```

Acknowledged findings are counted, never hidden, and errors cannot be
acknowledged at all. **Never acknowledge a finding on the user'"'"'s behalf without
asking.** Silencing a true objection is the one edit that makes this tool worse
than not having it.

**Never edit the YAML directly to change a status.** Use `set`, so the change is
previewed, verified, and journaled with the reason. Direct edits are for
structure (gates, contracts, published facts) only.

trellis owns the state machine. If a status is edited around it — by you, by
the user, by another tool — that is **drift**, and it is the editor's to
reconcile, not something the tool silently absorbs. Run `trellis drift` to see
it. If the user made the edit deliberately, `trellis drift --accept --because
"..."` records what the file now says and why. Never accept drift on the user's
behalf without asking why it happened: the reason is the whole point, and it is
the part a hand edit threw away.

## Corrections

A status moving backwards — `done` to `in_progress`, `agreed` to `draft` — is
not progress being undone. It is a **belief being revised**, and it is the most
informative thing that happens to a graph. Going forward is progress; dropping
work (`abandoned`, `superseded`) is a decision; only walking back is an error
being admitted.

The tool notices and asks why. **Answer it.** If you are running with `--yes`,
pass `--because "..."` instead. `doctor` reports a correction with no recorded
reason as *"that lesson is gone"*, which is exactly what it is.

This matters because revision and correction look identical in a diff and mean
opposite things. A contract revised nine times is being negotiated; a node
corrected twice was wrong twice, and what it claims now is worth less than what
an uncorrected node claims. `trust` reports them separately for that reason.

## Mistakes to avoid

- **Ending with "looks good."** Run `doctor` and report it. If it is silent on a
  fresh graph, say that silence means the graph is too small to disagree with.
- **Being thorough.** Completionism produces inert nodes. `doctor` flags them
  individually so the wall of output tells you what you did.
- **Marking things `verified`** that you heard in conversation. That is `stated`.
- **Writing `done` when the user said "basically done"** or "done but not
  reviewed." That is `done_unverified`, which exports `complete: true` and
  `done: false` on purpose.
- **Gating on another subsystem's internals** (`tools.streaming.done`) instead
  of a fact it publishes. `doctor` reports this as `reaches_inside`.
- **Declaring readiness.** It is computed. Run the tool.
- **Inventing schema.** If a field is not in this document, it does not exist;
  the loader rejects unknown fields rather than ignoring them.
- **Correcting silently.** Walking a status backwards without recording why
  throws away the only part of the event that was not already obvious.
