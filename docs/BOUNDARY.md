# What is trellis, and what is built on top of it

trellis is an engine. The useful things around it — pulling nodes out of a
codebase, checking a claim against Linear, producing a brief someone will
read — are **not trellis**. They are separate programs that talk to it.

This document is the line. Every "should this live in trellis?" question gets
settled against it.

## The kernel

trellis owns exactly this:

- the **declared model** — work, contracts, gates, published facts, provenance
- the **expression language**, and the fact that references in it are the edges
- **derivation** — readiness, rollups, violations, cycles — as a pure function
  of declared fields plus dependency exports
- the **fingerprint and cache** discipline that makes derivation exact, and
  the **structure hash** that makes a negative result expire exactly: a node's
  fingerprint answers *did this change*, the structure hash answers *could a
  relationship have appeared anywhere since I last looked*
- the **write loop** — validate, preview, confirm, verify, journal — because it
  is what protects everything above
- **`check`**: the falsifier

Nothing in the kernel calls the network. Nothing in it is time-dependent.
Both properties are load-bearing: the first makes it usable offline and
testable without mocks, the second is why a content-hash cache key is sound.

## The three interfaces

Everything else is one of three shapes. Each is safe **structurally**, not by
agreement — a plugin cannot violate the guarantee even if it tries.

| kind | in | out | why it is safe |
|---|---|---|---|
| **renderer** | a snapshot | an artifact | never handed a path to the graph |
| **extractor** | anything | a **Delta** | cannot apply it; must pass validate → preview → confirm |
| **corroborator** | a snapshot | **findings** | can only challenge; a finding never sets state |

### Renderers

Already built. JSON on stdin, artifact on stdout, declared in `trellis.toml`.
The graph is never passed as a path, so a renderer cannot modify source. That
is why it is safe to point one at code you did not write.

### Extractors

An extractor turns something — a repository, a design document, a
conversation — into a proposed `Delta`. It never writes. trellis validates the
delta, previews its consequences, asks, and only then applies it.

**An extractor must annotate every edge it proposes.** A `Delta` carrying an
edge without provenance is rejected at the boundary. This is not politeness:
of seven edges read out of prose on the first real graph, **two were wrong**,
while every edge taken from a tracker's `blocked_by` was right. Nodes can come
from anywhere. Edges are a claim, and a claim needs a source.

A code scanner emitting `how: inferred` is welcome. One emitting bare edges is
refused.

### What a plugin may rely on

Everything on the far side of these interfaces is somebody else's program, so
the payload is a contract rather than an implementation detail. Three rules
make it one:

- **`meta.payload_version` is the version a consumer keys on.** It changes when
  the payload gains, loses or renames a key — the only event that can break a
  reader. `engine_version` is a different number for a different audience: it
  changes when a *computation* changes and every cache entry must be dropped,
  which is none of a plugin's business. A plugin that reads `engine_version` is
  reading the wrong one and will churn for no reason.
- **The payload shape is pinned by tests.** Changing it means editing the
  expected set in the same commit, so the diff is the record that an interface
  change was intended rather than incidental.
- **Nothing in the payload can be stale relative to the graph it was taken
  from.** Whether anything changed is asked of everything the payload pins, not
  of the derived state inside it, so a `refs` index a corroborator joins on
  cannot silently be a version behind. Without that, every consumer would have
  to `--force` a fresh snapshot before reading one, which is the deduplication
  gone. The exceptions are the parts that read live and say so: the timestamps
  in `meta`, and `trust`.
- **Counts come with their reasons.** `acknowledged: 7` cannot be rendered into
  anything a person can act on, so `acknowledgements` carries the node, the
  code, the date and the *why*. A client that can only show a badge is a client
  that cannot help anyone decide anything.

The last one generalises: any number in the payload that a person would ask
"why?" about ships with the answer, or it is not worth shipping.

### Corroborators

A corroborator checks the declaration against a system of record and emits
findings: *this node says `in_progress`, Linear says Done*. It never resolves
the disagreement, because resolving it requires knowing which one is wrong,
and that is a judgement.

Same rule as everything else in the trust layer: **challenge, never set.**

## Research and execution are separate tracks

Exploring the graph counterfactually — *what if this edge were wrong, what if
this were not done yet* — is a first-class use of trellis, as heavily used as
recording what is actually true. See TRE-9.

**It does not change the requirements of the write loop.** The two are related
in concept and separate in intent, and the constraint runs one way:

> Research may not loosen execution. If exploration wants something the write
> path forbids, the answer is a separate read-only surface — never an exemption
> in the write path.

The concrete case, because it will come up. `Delta.EDITABLE_FIELDS` is scalars
only: rewriting a `gates:` block is YAML surgery, not a state update.
`Graph.with_overlay` has no such limit and already re-derives correctly from a
hypothetical gate, so *what if this dependency were not real* is answerable
today. The tempting shortcut is to add `gates` to `EDITABLE_FIELDS` so one code
path serves both. That trade is the wrong way round: it buys a convenient
research surface by weakening the loop that protects every recorded fact.

The write restriction and the what-if restriction are **different restrictions
that happen to share a type today.** Splitting them is the work; merging them
is the bug.

Two properties fall out, and both are non-negotiable:

- **a counterfactual never writes.** Not "should not" — cannot. It is a
  different surface, not a flag on `set`.
- **an unapplyable state must be labelled unapplyable.** The trust story rests
  on the preview being the same object that gets applied. Exploration that
  reaches states no write could produce is useful and must never be mistakable
  for a preview.

Nothing here loosens the kernel's own rules either: exploration is still pure,
still time-free, still exact. A research surface that needed the network or the
clock would not be a research surface, it would be a corroborator.

The first consumer of this is `queries.edge_sensitivity`, which asks what the
graph derives without one edge and is read by `reconcile` to decide which edge
to put in front of someone first. It rewrites a `gates:` block through
`with_overlay`, which is exactly what the write path refuses to do, and
`EDITABLE_FIELDS` is unchanged — the two restrictions stayed different
restrictions. It is a query in `queries.py` rather than a fourth interface
alongside renderers, corroborators and extractors, which is the shape the rest
of this document asks for: a consumer of the existing read surface.

## Provenance carries a source, not just a confidence

`how` says an edge was `inferred`. It does not say by whom. Once several
extractors are contributing, that becomes the question that matters, because
it makes calibration per-source:

> the code scanner's inferred edges were wrong 4 of 9 times; the Linear
> corroborator's were wrong 0 of 23.

That is how an ecosystem earns trust without anyone auditing plugin internals.
It is the difference between a set of interfaces and a pile of plugins.

## Arbitrary domain, not arbitrary vocabulary

trellis should model a research programme or a compliance rollout as readily
as software. It should **not** generalise below work / contract / gate /
published fact.

The diagnostics are the product, and they exist because the kernel knows what
a contract *is*. `undrafted_contract`, `reaches_inside`, `awaiting_decision`,
and "the implementer is gating on its own contract" are only expressible in a
model with those concepts. A domain-agnostic engine over arbitrary nodes and
arbitrary predicates can detect cycles and unmet conditions, and nothing else.

Generality below this line trades the entire edge for reach.

## Where the current code sits

Written down because three modules are on the wrong side of this line today,
and saying so is cheaper than rediscovering it:

- **`viz.py`** is a renderer living in-tree. `snapshot.py` imports it directly
  to provide the built-in `mermaid` format — a kernel module depending on a
  renderer, which is the clearest violation in the codebase.
- **`propose.py`** is an extractor living in-tree. It is already the right
  shape — it produces a `Delta` and applies nothing — and nothing imports it
  except the CLI. It carries the only optional dependency in the project.
- **`evidence.py`** is a corroborator living in-tree. It shells out to git to
  challenge declarations, which is exactly a corroborator's job.

They are in-tree because they were written before this line existed, not
because they belong there.

## The cost, stated honestly

Moving corroborators out has a real price: `doctor` currently merges findings
from the kernel, from `evidence`, and from the journal, and **ranks them
together** by urgency. Once findings arrive from a subprocess, an external
corroborator has to declare its own urgency, and a ranking that mixes trusted
and untrusted sources is harder to keep honest than one computed in a single
place.

That is not a reason to keep them in. It is the thing to design carefully.
