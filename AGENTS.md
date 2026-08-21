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

**Provenance** records why you believe an edge:

```yaml
evidence:
  agent.reflect: {how: verified, at: 2026-08-20}
  contract.stage_handoff: inferred
```

`how` is one of `verified` (checked against a system of record), `stated`
(someone told you), `inferred` (you worked it out), `assumed` (nobody said it).

## Bootstrapping a graph

### Do not ask them to list their work

Everyone can list their projects, and the list is worth almost nothing — it
produces nodes with no edges, which is a list wearing a graph's clothes.
`doctor` will tell you so, one line per node.

Ask the questions that produce **edges**, which people only recall when
prompted sideways:

- *What did you find out late that you wish you had known early?* — a missed
  edge is exactly what "found out late" means.
- *What are two teams currently assuming about each other?* — this is how you
  find contracts, especially the ones nobody has agreed.
- *What is waiting on a person rather than on work?* — an unagreed contract,
  usually.
- *What would you have to go and check before you believed that?* — provenance,
  and it tells you which parts of the graph are guesses.

### Model contracts first

Not work. Contracts come out almost one-to-one and take minutes, because **a
contract is a thing people argued about, so it is remembered precisely.** Work
is remembered vaguely, and edges are remembered wrongly.

Then the work that satisfies each contract — naming the contract makes its
implementers obvious. Then gates, which mostly write themselves once contracts
and work exist.

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
- `trellis doctor` — run this more often than you think.

**Never edit the YAML directly to change a status.** Use `set`, so the change is
previewed, verified, and journaled with the reason. Direct edits are for
structure (gates, contracts, published facts) only.

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
