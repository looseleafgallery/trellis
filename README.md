# trellis

Compute the state of a multi-project initiative from a graph of work, gates,
and contracts. You declare what exists and what each piece requires; trellis
derives what is blocked, what is ready, what a proposed change would unlock,
and where the illegal states are.

The engine is a pure computation — no model calls, no network, no daemon, and a
full evaluation of a graph this size is sub-millisecond. One optional command
(`trellis log`) puts a model in front of it to turn a sentence into a proposed
change, but the model only ever proposes: every consequence is still computed.

```
pip install -e '.[dev]'
cd examples/agent-loop
trellis state
```

## The model

Two kinds of node.

**Work** is anything you can finish: an initiative, a project, a stage, a
one-line change. Nesting is by `parent`, and progress rolls up from the leaves.

```yaml
id: agent.tool_exec
title: Tool execution stage
parent: agent
status: not_started
# not_started | in_progress | done_unverified | done | superseded | abandoned
gates:
  start: agent.plan.done and contract.tool_schema.live
  finish: has(tools.streaming, "streaming-results")
provides: [tool-results]
```

**Contracts** are the agreements between subsystems — a schema, a handoff
envelope, a gate two pipelines have to see the same way.

```yaml
id: contract.tool_schema
kind: contract
status: agreed               # draft | proposed | agreed | frozen
version: 2
satisfied_by: [tools.registry, tools.sandbox]
```

Making the agreement its own node is the point. If a gate between subsystems
is an edge, then N subsystems agreeing on it is N² edges and no single place
to look. As a node, producers `satisfy` it, consumers `require` it, the
agreement is one artifact you can diff, and bumping its version invalidates
exactly its consumers.

A contract is `live` only when it is agreed *and* everything implementing it is
done. Not-live splits into `unagreed` (waiting on people to decide) and
`pending` (waiting on work to land) — a stuck pipeline needs a different push
depending on which one it is.

## Published facts

A subsystem declares the facts it offers the rest of the graph. Everything else
gates on those, not on what is inside:

```yaml
id: tools
publishes:
  streaming_results: has(tools.streaming, "streaming-results")
```

```yaml
id: agent.tool_exec
gates:
  finish: tools.streaming_results     # not tools.streaming
```

Without this, any node anywhere can gate on `tools.streaming.done` — and the
moment it does, the tools subsystem can no longer be split, renamed, or
reordered without silently breaking a gate in a subsystem that never knew it
existed. Components can only "change freely" if their internals are not
addressable from outside.

`publishes` is to a subsystem what a contract is to a pair of them. Values are
ordinary gate expressions (or literal numbers/booleans), they land in the
node's exports alongside the built-ins, and their references become dependency
edges like any others — so caching, cycle detection, and `explain` all work on
them unchanged. Publishing a name that would shadow a built-in export is
rejected at load.

`trellis check` reports a gate that reaches inside a subsystem it is not part
of as `reaches_inside` — advice, not an error, since it is sometimes what you
mean. Sibling references within a shared parent, and a parent reading its own
children, are not flagged.

A caveat worth knowing: a parent still exports `progress` and `leaf_done`, so a
child completing does reach its parent's consumers one hop out even when the
published fact has not moved. Published facts decouple *structure*; they do not
silence rollup churn.

## Gates

Gates are boolean expressions over other nodes' exports, in a whitelisted
subset of Python syntax:

```
agent.plan.done and contract.tool_schema.live and contract.tool_schema.version >= 2
```

`done_unverified` is complete-but-unchecked. It exports `done: false` and
`complete: true`, so a gate saying `.done` stays conservative while one saying
`.complete` proceeds at risk — and which you chose is visible in the gate.
It counts toward neither side of a rollup, understating rather than
overstating. `superseded` is replaced-by-something-else rather than dropped,
and like `abandoned` it leaves the rollup denominator.

Available on any node: `done`, `complete`, `active`, `abandoned`,
`superseded`, `dead`, `provides`,
`children_done`, `progress`, `leaf_done`, `leaf_total`, plus whatever that node
publishes. Contracts add `live`, `agreed`, `frozen`, `version`. Helpers:
`has(node, "tag")`, `all_done(...)`, `any_done(...)`, `count_done(...)`,
`at_least(n, ...)`.

Gate names are open: `start` and `finish` have meaning to the engine (`start`
drives readiness, both are checked against declared status), but any other name
you use is evaluated and reported too — useful for tracking a `review_passed`
or `security_signoff` condition without letting it gate readiness.

**The references in an expression are the dependency edges.** There is no
`depends_on:` list, because a hand-maintained one drifts away from the
requirement that actually matters.

`start` and `finish` are separate gates, and a node can be startable long
before it is finishable. Declaring something `done` behind an unsatisfied gate
is not silently accepted — it is reported as a violation, which is usually how
you find out two subsystems disagreed about what "done" meant.

## Commands

| | |
|---|---|
| `trellis check` | validate the graph; list every violation |
| `trellis state [node]` | derived state, as a tree or for one node |
| `trellis ready` | work whose start gate is satisfied right now |
| `trellis explain <node>` | why it is blocked, down to root causes |
| `trellis impact <node> --set status=done` | what-if: diff the whole system |
| `trellis deps <node> [-r]` | dependencies, or dependents |
| `trellis set <node> status=done` | change declared state, with a preview first |
| `trellis log "<what happened>"` | describe it in prose; a model proposes the delta |
| `trellis history` | what has been applied, and why |
| `trellis trust` | challenge the declaration: what is stale, what churns |
| `trellis doctor` | everything that looks wrong, with what to do about it |
| `trellis stats` | cache and recomputation counters |

Add `--json` to any of them.

`impact` is the one that answers "I moved one piece, what happens":

```
$ trellis impact tools.sandbox --set status=done
what if -> tools.sandbox: status=done

unlocks:
  > tools.streaming  Streaming tool results
contracts that go live:
  + contract.tool_schema

cost: recomputed 5/13 nodes (8 reused from cache)
```

Change several things at once by prefixing a node id: `--set status=done --set
'agent.plan@status=done'`.

## The write loop

Reading the graph is only half of it. `set` and `log` close the loop, and both
run the same four steps:

```
propose  ->  validate  ->  preview  ->  confirm  ->  write
```

`set` is the deterministic entry point:

```
$ trellis set tools.sandbox status=done
proposed:
  ~ tools.sandbox  status: in_progress -> done

unlocks:
  > tools.streaming  Streaming tool results
contracts that go live:
  + contract.tool_schema

cost: recomputed 5/13 nodes (8 reused from cache)

apply? [y/N]
```

`log` is the same loop with a model on the front, mapping a sentence onto node
ids and status values:

```
trellis log "finished the sandbox work and we signed off on the tool schema"
```

**The model only proposes.** It maps prose onto ids and statuses, and stops. It
never decides what a change unblocks, whether a gate is satisfied, or what
state the system lands in — the engine computes all of that, exactly as it does
for a change you type by hand. A proposer that is only asked to do the part
requiring language cannot be wrong about the part requiring logic. Anything it
cannot confidently map comes back as `unmatched` rather than a guess, and every
proposal is shown with its confidence before you accept it.

The preview is not a separate rendering of what is about to happen — it is
`queries.impact` over the very overlay that gets written, so it cannot drift
from the result.

Requires the optional extra and credentials: `pip install -e '.[llm]'` plus
`ANTHROPIC_API_KEY`. The core engine never imports it.

### How writes land

Your YAML is written and read by people: comments, `notes: >` blocks, a
deliberate ordering. A parse-and-dump round trip would quietly reformat all of
that, so `trellis` edits the specific line and leaves every other byte alone —
a status change is a one-line diff.

Line surgery is only safe with a check behind it. After every write the graph
is reloaded and verified: the intended change landed, and no other node's
fingerprint moved. If either check fails, the original bytes are restored and
the write is reported as failed. Only scalar fields (`status`, `version`,
`title`, `parent`) are writable; rewriting a `gates:` block is a structural
edit and belongs in your editor. New nodes are written to their own file rather
than appended into an existing one — appending means guessing at the
surrounding document's shape.

Every applied change is appended to `.trellis/journal.jsonl` with the sentence
that produced it. The YAML holds current state and git holds the diffs; neither
holds the *why*.

## Bootstrapping, and agents

There is no `trellis init` wizard. The hard part of starting a graph is the
interview, and a scripted question flow cannot follow up, cannot probe a vague
answer, and starts cold — while an agent already working in your repo has read
the design docs and been in the conversation.

So the procedure lives in [`AGENTS.md`](AGENTS.md), written for an agent to
execute with you. It covers the questions that actually produce edges (asking
someone to list their projects does not), why contracts bootstrap first and
nearly free, and why a first graph should stop at about eight nodes.

One thing in it is load-bearing enough to repeat here: **the agent transcribes
the tool's objections rather than generating them.** Ending a bootstrap by
disagreeing with the user is what makes it worth doing, and it is exactly what
an agent is least reliable at unprompted — so the last step is `trellis
doctor`, and the disagreement is mechanical:

```
$ trellis doctor
2 thing(s) look wrong to me:

  . contract.stage_handoff: still proposed but 1 node(s) gate on it (agent.emit)
      -> nobody has agreed this. Ask both sides whether it is settled.
  . agent.emit: its edge to contract.stage_handoff is inferred and was never confirmed

none of this changed any state. these are questions, not corrections.
```

`doctor` is `check` plus `trust` plus a remedy for each finding — a code says
what is true, a remedy says what to ask. On a graph too small to have anything
wrong with it, it says that rather than congratulating you.

Every YAML example in `AGENTS.md` and this README is parsed by the test suite,
so a doc that drifts from the schema fails CI rather than teaching an agent to
write files that do not load.

## Trust

Evaluation is the easy half. The half that decides whether anyone still uses
this in week three is whether the declaration is honest — because a graph
nobody trusts is worse than no graph, being confidently wrong rather than
obviously absent.

`trellis trust` challenges the declaration using two signals that cost nothing
and integrate with nothing:

```
$ trellis trust
stale - claims to be moving but has not changed in 14+ days:
  ! agent.plan          in_progress   unchanged 21d (via git)

churning - revised well above this graph's median:
  ~ contract.tool_schema   9 revisions

these are challenges, not corrections - nothing here changed any state
```

**Volatility** is revision count of the declaring file, from `git log`. A
contract at 9 revisions against a median of 2 is not the same contract as one
written once and left alone, even though both currently read `agreed`. This is
what turns a mechanical impact answer into a decision — `impact` annotates what
it unlocks:

```
contracts that go live:
  + churny.contract   [declaration revised 9x - check it is current]
```

**Age** is how long a declaration has sat unchanged, taken from the journal
where it knows the specific node and from git otherwise. Only declarations that
*claim motion* are challenged: `in_progress` untouched for three weeks is a
lie, `not_started` untouched for three weeks is accurate.

Everything here **challenges, never sets.** Nothing changes a status or opens a
gate. Where the graph is not in git, every signal degrades to "unknown" rather
than to a wrong answer.

Two deliberate limits. Volatility is per *file*, so a file holding several
nodes reports a shared count and says so — one node per file buys per-node
resolution. And volatility is not classified at all below four nodes with
history: an outlier needs a baseline to be an outlier against, and in a tiny
graph the churning node dominates its own median.

### Edge provenance

An edge checked against a system of record and one read out of a prose sentence
render identically without help, so a reader trusts them uniformly and should
not. Annotate them:

```yaml
id: agent.emit
gates:
  start: agent.reflect.done and contract.stage_handoff.live
evidence:
  agent.reflect: {how: verified, at: 2026-08-20}
  contract.stage_handoff: inferred        # shorthand for {how: inferred}
```

`how` is a closed set, most trustworthy first: `verified` (checked against a
system of record), `stated` (someone said so), `inferred` (read out of prose,
never confirmed), `assumed` (nobody said it). Closed on purpose — an open
vocabulary could not be reported consistently.

`explain` then distinguishes them, which turns a blocked node into an
instruction:

```
- agent.reflect.done  ->  agent.reflect is blocked   [edge verified 2026-08-20]
- contract.stage_handoff.live  ->  ... [edge inferred, never confirmed - check this]
```

and `trust` reports the two provenance failures:

- **unconfirmed edges** — believed on inference or assumption, never checked.
- **stale verifications** — `at` is a shelf life, not a stamp. An edge was true
  against the thing it was checked against, at that moment; that thing can move
  afterwards without anything here noticing. This is the green-but-wrong CI
  check, expressed in the model.

Annotating an edge deliberately does **not** invalidate a cache entry —
provenance changes how a result is reported, never what it is. If annotating
cost a recomputation, nobody would annotate anything.

Coverage is reported as one line, and unannotated edges are only listed once a
graph annotates anything at all: gaps mean something after you have started,
and nagging before that is noise.

### Why trust is a separate layer

Age depends on the wall clock, and the engine's cache key is a hash of its
inputs. Letting "today" into that hash would mean entries silently expiring at
midnight, or a cache hit returning yesterday's answer. So the engine stays
time-free and exactly cacheable, and everything time-dependent lives outside it
and is recomputed per query — which is cheap, because it is one `git log` call.

`check` stays time-free too: it answers *is this declaration well-formed*.
`trust` answers *should you believe it*.

## Why it stays cheap

Every node's derived state is a pure function of its own declared fields plus
the **exports** of its dependencies — nothing else. The cache key is a hash of
exactly those inputs, so a hit is exact by construction and there is no
invalidation pass to get wrong.

Exports are deliberately coarser than derived state. A node publishes whether
it is done and what it provides, not its full record. So when a stage goes
`blocked -> ready`, its exports do not change, its dependents' keys do not
change, and the walk stops dead. This *early cutoff* — not the memo store — is
where the efficiency comes from; a memo store alone would still rehash the
whole downstream cone on every edit.

The cache is persisted to `.trellis/cache.json`, so reuse spans invocations.

For a graph of a few hundred nodes none of this is load-bearing yet — Python
recomputes it faster than it reads the YAML. It is built now because the cache
key is the part that has to be right *before* an expensive evaluator exists.
When a gate is eventually judged by a model rather than an expression, the key
already isolates precisely the inputs that could change that judgment, and only
the nodes whose inputs moved pay for a re-run.

Proposals are cached the same way, keyed by the prompt version, the model, the
graph inventory, and the text — so repeating a sentence against an unchanged
graph costs nothing, and any change to either invalidates it.

## What it does not do

No model-judged gates. A requirement like "the retriever is fast enough" cannot
be expressed as an expression today. The engine is shaped for it — a
model-backed gate is just another evaluator behind the same cache key — but it
does not exist yet.

No bootstrapping from an existing repository. You write the graph by hand or
grow it through `log`; nothing reads your code, issues, or PRs to propose one.

- **Partial structural critique.** `check` flags reaching inside a subsystem,
  contracts nobody drafted or implements, work belonging to no parent, and
  inert nodes that require nothing and are required by nothing. The wider pass
  — nodes that always move together in the journal, heavy fan-in that wants to
  be an explicit interface — is not built.

No structural writes. Gates, `provides`, and `satisfied_by` are edited in your
editor, not through the CLI.

## Layout

```
trellis/model.py     declared nodes, graph, edge extraction
trellis/expr.py      gate expression parsing, evaluation, traces
trellis/engine.py    incremental evaluator, exports, violations
trellis/cache.py     content-keyed memo store
trellis/queries.py   check, ready, explain, impact
trellis/delta.py     a proposed change, and its validation
trellis/edit.py      YAML line surgery, with verify-or-restore
trellis/journal.py   append-only record of what was applied
trellis/evidence.py  volatility, staleness, provenance: challenge, never set
AGENTS.md            how an agent should bootstrap and maintain a graph
trellis/propose.py   prose -> proposed delta (the only model call)
trellis/cli.py       command line
examples/agent-loop  a pipeline with cross-subsystem contracts
```
