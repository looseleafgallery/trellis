# Changelog

Notable changes to trellis. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semver](https://semver.org/spec/v2.0.0.html).

Until 1.0 the graph schema may change between minor versions. Schema changes
will always appear here, because a graph you already wrote is the thing most
expensive to break.

## [Unreleased]

### Added

- `meta.payload_version` on the snapshot payload, so a plugin can refuse a
  payload it does not understand instead of reading a missing key as an empty
  one. Deliberately separate from `engine_version`, which changes when a
  computation changes and every cache entry must be dropped - an event no
  consumer should care about.
- `acknowledgements` in the payload: node, code, date and **why**, so a client
  can show why a finding was answered rather than only how many were. A count
  cannot be rendered into anything a person can act on.
- The snapshot payload shape is pinned by tests. It is the plugin contract -
  what a renderer reads on stdin and what any human-facing client is built
  against - and it was the larger of the two interfaces left unguarded.
- Every answer in `review` states its **side effect** at the moment of
  choosing, not in documentation read afterwards: what it writes, which file
  it lands in, whether it is permanent, and who else ends up seeing it. A
  marker column separates the options that touch disk from the ones that do
  not. `acknowledge` reads like dismissing a notice and is in fact a permanent
  ruling everyone who clones the repo sees - that difference decided five
  findings on one real graph, in the wrong direction.
- `check` reports **why** each finding was acknowledged, not just how many.
  The reason is captured at the one moment it is cheap and was previously
  never read back, which made the most informative field in the graph the
  least visible one. An acknowledgement made without a reason says so rather
  than showing a blank.
- `AGENTS.md` gains *Where trellis belongs in your own work* - read the graph
  before asking the user what is open, model structural work before proposing
  it, run `impact` before claiming a change unblocks something, and go through
  `set` so the reason survives.
- `review` shows the node before asking you to rule on it: title, declared
  status and derived readiness, `ref:`, how many nodes depend on it, where it
  is declared, and the `notes:` the modeller wrote. All of it was already in
  the graph and none of it was printed - `core.concurrency`'s note says the
  decision belongs to whoever owns the node, which is the answer to the
  finding being raised.
- `ref:` is printed plainly. Turning `TRE-7` into a URL means knowing which
  tracker a ref belongs to, and the kernel deliberately does not; that belongs
  in configuration or a client.
- The `--json` payload shape is pinned by tests. It is an interface: the human
  CLI is one client and everything else reads the JSON, so a key that quietly
  changes name breaks every consumer at once with no error anywhere. Values
  stay free to change; keys are the contract, and changing one now means
  editing the expected shape in the same commit.
- `review` asks about one **node** at a time rather than walking a flat list of
  findings. Ranking by urgency alone split a node's findings apart, so a node
  could come back several findings later with nothing saying you had already
  made three decisions about it. Nodes are still ordered by their most urgent
  finding, so the first node is still the one to start with.
- `A` acknowledges every remaining finding on the current node, asking for the
  reason once. "This node is fine, stop asking" was previously the same
  decision typed once per finding.
- The option descriptions are shown in full the first time a key appears and
  compacted afterwards, with `?` to bring them back. Explaining them under
  every finding is what turned them into noise; explaining them only on the
  first finding would have hidden `a`, which never appears on an error.
- Decided the distribution name: **`trellis-kernel`**. `trellis` on PyPI is an
  abandoned 2008 alpha for an unrelated project, so a PEP 541 reclaim request
  is worth making, but nothing should wait on it. The import, the command and
  the tool stay `trellis`; only the distribution differs.
- The interactive loops say what each answer *does*, not just what it is
  called: acknowledging answers a finding for good, skipping defers it to the
  next run, quitting keeps everything answered so far. The remedies already
  arrived as instructions; the moment a person has to act now does too.
- The `edit` option says when `$EDITOR` is unset instead of offering a choice
  that fails when taken.
- A durable proposal queue. `set`/`log --propose` queue a validated, previewed
  delta instead of writing it; `pending`, `accept` and `reject` decide it later.
  Stored in `history/proposals.jsonl`, committed and append-only — a proposal
  awaiting a decision is a handoff between two parties, so it cannot live
  somewhere the other party cannot see.
- Accepting **recomputes** the consequence against the graph as it is now
  rather than replaying the preview captured at propose time. A node that moved
  underneath is refused by fingerprint and named; a proposal whose *consequence*
  changed is re-previewed rather than refused, because a graph moving around a
  live proposal is normal.
- Rejections are kept with their reason, and re-proposing the same change says
  when it was turned down and why. Told, not refused: the same change can be
  right later.
- `trust` challenges a proposal left undecided for three weeks. A queue nobody
  empties is a worse place for a decision than the prose it replaced, because
  it looks handled.
- `trust` reports provenance calibration split by `how` — `inferred` and
  `assumed` are guesses of different confidence and an aggregate over them
  answers no question anyone has. Ranked most-wrong first, which is the order
  someone would work through them. Counts at every level, including in `--json`
  where a consumer could divide them itself.
- Calibration is reported whenever anything has been checked, not only while
  unconfirmed edges remain. A graph whose edges have all since been confirmed
  is where the number is most worth seeing, and it was the one case that
  printed nothing.
- `last checked` states the age of the evidence. The counts stay all-time on
  purpose: windowing them would shrink a denominator that is already small,
  so the age is stated rather than used to discard anything.
- Corroborators: an external program that takes a snapshot and returns
  findings, declared in `trellis.toml` and merged into `doctor`. It joins on
  `ref:` and can never set state. Limited to `info` and `warn` — `error` means
  the graph cannot evaluate, which only the kernel establishes. A corroborator
  that fails to run reports that as a finding rather than passing silently.
- `evidence:` accepts `by:`, naming what believed an edge — an extractor, a
  corroborator, a tool. `reconcile` records it on the outcome so it survives
  the edge being deleted, and reports calibration per source once more than one
  thing has annotated anything. Absent means a person, directly.

- `trellis reconcile` walks believed edges and records whether each held, with
  the reason. Kept in the journal so it survives the edge being deleted — which
  is what happens to an edge that turned out to be wrong. `trust` then reports
  how many checked edges were wrong, as counts rather than a rate.

- `trellis graph` draws in the terminal by default, as a dependency tree read
  top-down from what is waiting to what it waits on. `-f mermaid` keeps the
  source for pasting into an issue, and `-f html` writes a self-contained page
  with the graph embedded in it.

- `ref:` on a node records which external item it *is* — a ticket id, a URL,
  whatever the rest of your world addresses the work by. Opaque and optional:
  never fetched, never parsed, no assumption there is one tracker. A ref works
  anywhere a node id does (`trellis state ENG-1552`), node ids always win, and
  a ref naming two nodes resolves to neither rather than guessing. `state
  --ref` shows the column, `--json` carries it on every node record, snapshots
  keep it, and `set <node> ref=...` writes it through the normal loop. `check`
  reports a shared ref as `duplicate_ref` (info, not an error — splitting one
  ticket across two nodes is legitimate; only the join is ambiguous). Excluded
  from the fingerprint, so annotating a graph invalidates no cache entry.
  Identity, not grounding: it says which thing this is, never whether the
  claim still holds.

- `awaiting:` on a work node records that a decision is owed before it can
  move. Readiness becomes `awaiting` rather than `ready`, `trellis ready`
  excludes it, `explain` says *waiting on a decision, not blocked by work*, and
  it goes stale like any other declaration.
- `trellis snapshot` freezes derived state as a point-in-time record — the one
  thing git cannot give back, since the trust layer computes against today.
  Content-addressed, never refreshed in place, indexed with age first.
- Snapshot renderers: any executable reading the snapshot as JSON on stdin and
  writing an artifact on stdout, declared in `trellis.toml`. Never given a path
  to the graph, so read-only is structural. `json` and `mermaid` built in.
- `trellis review` walks findings one at a time with an action on each:
  acknowledge (with a reason, journaled), a direct fix where the remedy is
  unambiguous, or open `$EDITOR` at the node. Re-reads the graph after any
  change and skips findings the change already resolved.
- `trellis drift` reports statuses changed outside the loop, naming the ones
  that walked backwards as unrecorded corrections. `--accept` records what the
  file now says, and why.
- `trellis blocking <node>` reports what a node is holding up as two numbers —
  what unlocks immediately, and what is blocked downstream behind it — because
  conflating them is a real and repeated reporting error. `--all` ranks
  chokepoints.
- `trellis graph` renders a slice as mermaid for pasting into an issue or PR.
  Refuses slices too large to read.
- `acknowledge:` on a node answers a finding for good. Acknowledged findings
  are counted rather than hidden, stale acknowledgements are reported, and
  errors cannot be acknowledged — a graph that cannot evaluate must not be able
  to look clean.

### Changed

- `trellis graph` limits are per format and much higher: 120 nodes as a tree,
  40 as a diagram. They are about readability rather than cost — 800 nodes
  render in about a millisecond. Tree depth is capped at twelve levels, and a
  cut branch says how many nodes are below it.

- The journal moved from `.trellis/` to `history/`, which is committed. It is
  the only copy of why anything changed, and `.trellis/` is normally
  gitignored, so every recorded reason was local to one machine. The cache
  stays in `.trellis/` and stays ignored. A journal in the old place is still
  read and `check` reports the move to make; a graph with no journal now says
  so instead of quietly answering a weaker question.

- `AGENTS.md` bootstrapping guidance rewritten around three starting conditions
  (blank page, tracker only, tracker plus documents), which need the interview
  questions in near-opposite proportions. Adds the measured rule that edges
  should come from a system of record, and the reconciliation step that has to
  accompany extracting them from prose.

- Findings are ranked by what to fix first rather than only by severity, and
  `check` ends by naming the one to start with.

### Fixed

- `trellis deps --json` printed a heading before the payload, so its output was
  not JSON and no consumer could parse it. Found by the new interface guard on
  its first run. It now emits `{node, direction, nodes}` and nothing else.
- Ctrl-C is a stop, not a crash. `KeyboardInterrupt` reached the user as a raw
  traceback from any command, which left nobody able to tell whether anything
  had been written. It now says so plainly and exits 130.
- `reconcile` records each judgement when it is made rather than when the loop
  finishes. `q` kept every answer while Ctrl-C threw them all away - two ways
  out of one loop with opposite consequences, and these are judgements a person
  cannot reproduce. One path now.
- `trust` summarised calibration as "last time you checked, N of M were wrong"
  while counting every pass ever recorded. It named a single pass and reported
  all of them — a conclusion the tool had not verified, in its own output.
- A `#` inside a quoted value was treated as the start of a trailing comment
  when rewriting a field, so `ref: "#20"` became `ref: TRE-5  #20"`. YAML read
  that as `TRE-5` plus a comment, which meant the value was right, every test
  passed, and the verify step approved a file it had corrupted.

- A free-text field given something that is not one value is refused instead
  of coerced. `ref: [ENG-1599, ENG-1600]` used to load clean, render as the
  Python repr `['ENG-1599', 'ENG-1600']`, and never resolve — a value that
  validates, displays, and compares as a perfectly good string while meaning
  nothing. `title`, `awaiting`, and `notes` were coerced the same way; `ref` is
  simply where it became visible, being the only one with a lookup behind it.
  A bare numeric ticket id (`ref: 1552`) is still accepted, since YAML reads it
  as an int and writing one is normal; a YAML boolean (`ref: yes`) is refused
  by name, being the same trap as `ref: #39` reading as a comment.

- `check` reports `shadowed_ref` when a node's `ref:` is also a node id. Node
  ids win any lookup, which is what keeps declaring a ref from changing what an
  existing command means — but the consequence used to be silent, so the only
  way to discover the dead join was to try it and get somebody else's node
  back, which looks like a correct answer. Info, not an error: nothing is
  broken and the other node is returned correctly. Only the join is dead.

- Tests no longer inherit the machine's global git config. On a machine with
  commit signing enforced, eleven fixtures errored before asserting anything
  and named gpg rather than the fixture, so a contributor's first `pytest`
  looked like their own git setup was broken.

- `file_history` passed a relative pathspec to `git -C <graph_dir>`, so the
  path was resolved twice — `--graph graph` from a project root asked git for
  `graph/graph`, matched nothing, and silently reported every node as having no
  history. Volatility and staleness were off for anyone using the natural
  spelling.
- A journal written before the path fix lives where the *buggy* resolution put
  it, which the legacy reader did not look at — so upgrading could make
  recorded reasons invisible while the file sat tracked in the repository. Both
  old locations are now read, and `check` names the one it found.
- Two messages stated conclusions the tool had not verified: `trust` reported
  "not in git, or never committed" from a lookup that could not have found
  anything, and `drift` reported "trellis has not written any status yet" when
  it had only not written where it looked.

- The journal, cache, snapshots and `trellis.toml` were located relative to how
  `--graph` was spelled rather than to where the graph is, so `.` and `graph`
  were two different projects. One graph could accumulate two journals with
  half the history in each, and `drift` would report a change made through
  trellis from another directory as an edit made outside it. Paths are resolved
  now, so durable state belongs to the graph rather than to the invocation.

- Every command that reads derived state crashed with a traceback on a graph
  containing a cycle. `ready`, `doctor`, `trust`, `blocking`, `graph`,
  `explain`, `stats`, `snapshot` and `set` now report the cycle and point at
  `check`, which is the command that explains it.
- A node id containing anything but letters, digits, underscores and dots
  cannot be referenced by a gate, because expressions are parsed as Python and
  `a-b` reads as subtraction. `check` now says so, and a reference that split
  on one suggests the node it meant.

- Cycles caused by gating on your own ancestor's published fact, or by an
  implementer gating on the contract it satisfies, now name the mistake and the
  fix rather than only the topology (#11, #12).
- `unconsumed_contract` no longer fires when the only node gating on a contract
  is its own implementer — it contradicted the cycle reported alongside it
  (#12). Contract demand is now measured by references rather than by
  dependents, so a parent no longer counts as a consumer.
- `dangling_evidence` recognises a published-fact name and points at the node
  that publishes it (#13).
- Running on Python older than 3.11 raises a message naming the version instead
  of failing later on a missing PyYAML (#10).

### Added

- Work and contract nodes, gate expressions, and an incremental evaluator with
  early cutoff.
- Published facts (`publishes:`) as the subsystem encapsulation boundary.
- The write loop: `set` and `log`, each previewing with `impact` before writing.
- Trust layer: `trust` reports stale and churning declarations from git history.
- Edge provenance (`evidence:`) distinguishing checked edges from guesses.
- Corrections as a distinct event from revisions, with the reason captured at
  the moment the belief changes.
- `doctor`: everything that looks wrong, with a remedy per finding.
- `AGENTS.md`, the agent-first bootstrapping procedure.

[Unreleased]: https://github.com/looseleafgallery/trellis/commits/main
