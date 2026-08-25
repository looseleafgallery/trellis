# Working with trellis

You are reading this because someone wants you to build or maintain a trellis
graph for them. This is the operating manual for doing that.

The division is deliberate: **instructions live here, properties live in
`README.md`.** If it says *you should do X*, it is in this file. If it says *the
tool guarantees Y* — the model only proposes, evidence never sets, errors cannot
be acknowledged away — it is in the README, because that is what a person reads
to decide whether to trust the thing.

## The one rule

**You transcribe the tool's objections. You do not generate them.**

A trellis graph is worth having only if it is honest, and the thing that makes
it honest is that it disagrees. That disagreement has to come from `trellis
doctor`, not from your judgment — because your judgment is agreeable, and a
bootstrap that ends with "here is your graph, looks good" has taught the user
nothing and will not be opened again.

So: you ask the questions, you write the YAML, and then you run `trellis
doctor` and report what it says. If it says nothing, say that too — and read
the scope it prints with it, because most of what "nothing looks wrong" means
on a fresh graph is that most of the checks had nothing to run against:

```
$ trellis doctor
nothing looks wrong across 6 nodes.

checked:
  - structure: gates, references, contracts, cycles, rollups
  - age and staleness: all 6 declaration(s), dated from git
  - volatility: 6 declaration(s), against this graph's own median

not checked here, so nothing is claimed about it:
  - corrections and drift: this graph has no journal
  - edge provenance: none of 3 edge(s) carry `evidence:`
  - anything outside trellis: no corroborators are configured

a clean result is only as wide as what it compared.
```

Report the second list as well as the first. A graph nothing could be checked
against is not a graph that checked out, and you are the only one in a position
to say so before they believe it.

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

**`ref:`** is where an external id goes — the ticket, row, or document the rest
of their world addresses this work by. Optional, opaque, never fetched:

```yaml
id: safety.d1
title: External-gate contract v1
ref: ENG-1552
status: in_progress
```

Put the id there, **not in the title**. A title is the field most likely to be
reworded, and a ref buried in one breaks every join the moment it is.

**One node, one ref** — a list is refused, not coerced. If a piece of work is
tracked as two tickets, name the second in the title or split the node. Quote
anything YAML would read as something other than text: `"#39"` is a comment
otherwise, and `yes` / `no` / `on` / `off` are booleans. A bare number is fine.

There is no `owner:` field; who work waits on is a tracker's job, and
`awaiting:` covers a decision being owed.

## Grammar you will get wrong from memory

Copy from here rather than recalling.

**Statuses.** Work: `not_started`, `in_progress`, `done_unverified`, `done`,
`superseded`, `abandoned`. Contract: `draft`, `proposed`, `agreed`, `frozen`.

**Node ids must be reachable by a gate.** Letters, digits, underscores and
dots only. Gate expressions are parsed as Python, so `svc.a-thing` reads as a
subtraction and the reference silently becomes two unknown names. Use
`svc.a_thing`. `check` reports an unreachable id rather than letting you find
out that way.

**Write a node as a block mapping, one field per line.** `{id: a, status:
done}` is what you get by default from anything dumping a dict, and the loader
refuses it, naming the line: it reads correctly and no write can ever land on
it, because `set` and `accept` rewrite a single field's line. A flow *value*
inside a block node - `evidence: {how: verified, at: 2026-08-20}` - is fine.

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
`superseded`, `dead`, `awaiting`, `provides`, `children_done`, `progress`,
`leaf_done`, `leaf_total`. Contracts add `live`, `agreed`, `frozen`, `version`.

**A node waiting on a person, not on work:**

```yaml
id: a.thing
status: not_started
awaiting: which of the two storage backends we standardise on
```

Its readiness becomes `awaiting` rather than `ready`, and `trellis ready`
excludes it — the gate is open, but nobody can actually pick it up. Free text
describing *what* is owed; **do not put a person'"'"'s name in it.** Who owes a
decision is a tracker'"'"'s job. Clear it with `trellis set <node> awaiting=none`
once the call is made.

Blocked-by-work outranks it: if the gate is shut, the work is the truth.

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

`by` names what believed it — an extractor, a corroborator, a tool. Omit it
when the answer is "a person, directly", which is what an absent `by` means.
**If you are an automated extractor writing edges, set it**: calibration is
only actionable per source, and an edge written without one can never be
attributed afterwards.

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

When a node came from a record, put that record's id in `ref:` as you write it.
It costs nothing at the time and it is what makes the reconciliation pass a
join rather than a second reading — and reconciliation is the step that catches
the wrong edges.

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

## Where trellis belongs in your own work

The commands below are for maintaining someone's graph. This section is about
using it while you work, which is a different habit and the one that gets
skipped.

**At the start of a task, read the graph before asking the user anything.**
`trellis ready` and `trellis check` answer "what is open" from the declaration
rather than from a summary you generate, and the answer is identical every
time it is asked. Re-deriving it in prose produces a slightly different, very
dense answer on every run, and nobody can tell whether a change in the wording
means a change in the work.

**Before proposing structural work, model it.** Write the nodes and gates
first, then run `check`. The tool objects to a bad model in a second, which is
cheaper than a person objecting to it in review — and cheaper still than
nobody objecting and the shape being wrong for a month. If your model needs a
relationship nobody has worked out yet, that relationship is a **contract in
`draft`**: name it, gate on it, and the work is correctly blocked on a
decision rather than looking startable.

**Before a change, run `impact`.** It reports what a status change unlocks,
what it blocks, and which contracts go live. Saying "this unblocks the API
work" without running it is exactly the class of unverified conclusion this
project exists to catch.

**After a change, go through `set`.** Never hand-edit a status. The write loop
previews, verifies and journals the reason; a hand edit is drift and throws
away the *why*, which is the part nothing else records.

**When the user is not there, `--propose`.** Covered below.

**Never `acknowledge`, `accept` or `reject` on the user's behalf.** Those are
rulings, and a ruling you make for someone is a guess wearing a decision's
clothes.

The test for whether you used it: at the end of a task, could a person run
`trellis check` and see what you did and why, without reading the conversation?
If not, the work happened outside the loop.

## Ongoing use

- `trellis ready` — what can be picked up now. Anything `awaiting` a decision
  is deliberately absent; report those separately if the user asks what is
  outstanding, because they need a person rather than an engineer.
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
- `trellis reconcile` — walk the unconfirmed edges and record whether each held,
  most depended on first. Each is offered with two counts and the nodes they
  counted: how many nodes' derivation reads through the edge, and how many
  derive differently if it is not real. **For the user, not for you**: you
  cannot check an edge against a system of record on their behalf. Suggest it
  when `trust` lists unconfirmed edges, and never guess an outcome.
- `trellis blocking <node>` — what it is holding up. Reports two numbers:
  what unlocks the moment it lands, and what is blocked downstream behind it.
  **Never quote the second as the first** — they answer different questions,
  and conflating them is how people misreport their own graph.
- `trellis blocking --all` — chokepoints, ranked.
- `trellis graph --around <node>` — draws in the terminal. Add `-f mermaid`
  for a slice to paste where someone else can read it. Useful when the person you need agreement from does not have
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

`check` will then ask why, because an acknowledgement with no reason says only
that somebody once decided something. You have no terminal, so `review` cannot
ask you — say it in the file, beside the code:

```yaml
acknowledge:
  - code: inert_node
    why: spike only, one ticket, nothing gates on it yet
```

Both forms are accepted and can be mixed in one list. Write the reason the user
gave you, in their words — not a label, and not your reconstruction of it. If
you cannot say what makes the finding permanently true, you do not yet have
the acknowledgement to write.

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

## When the person is not there

You will often finish modelling in a session the user is not in. A change you
believe in but cannot get confirmed does not go in your summary and it does not
go in `-y`. Queue it:

```
trellis set api.schema status=done --propose --because "tests green, awaiting review"
```

It is validated and previewed exactly as a write would be, then parked in
`history/proposals.jsonl` where the user will find it with `trellis pending`.

**Never `accept` a proposal on the user's behalf.** Queuing exists precisely
because the decision is theirs; accepting your own proposal is `-y` with extra
steps, and it launders a guess into a decision the journal will record as
considered. The same goes for `reject` — turning down a proposal somebody else
queued is not yours to do either.

If re-proposing something surfaces a prior rejection, **read the reason before
proposing it again**. It may still be right, and it may be the same mistake.
Either way the user should hear that it was refused before, from you, rather
than discovering it in the queue.

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

## When a session teaches you something

Not every finding is a change. If working on a graph teaches you something that
would otherwise be relearned — a message that misled, an edit that failed
quietly, a rule nobody had written down — it needs a home, or it is gone when
the session ends.

For the trellis repository itself, that home is the "Lessons that cost us
something" section of `CONTRIBUTING.md`. For a user's own graph, ask them where
it belongs; a `notes:` field on the node it concerns is often right, and the
reason on a correction is often better.

The failure to avoid is leaving it in the conversation. A lesson only one
session knows is a second place to be wrong, which is what this tool exists to
remove.

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
