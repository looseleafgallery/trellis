# Changelog

Notable changes to trellis. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semver](https://semver.org/spec/v2.0.0.html).

Until 1.0 the graph schema may change between minor versions. Schema changes
will always appear here, because a graph you already wrote is the thing most
expensive to break.

<!-- New entries are collected in here from changelog.d/ when a version is
     cut. scriv reads no further than the end marker, so the [Unreleased]
     section below - which predates the fragments and is TRE-6's to resolve -
     is left alone. -->
<!-- scriv-insert-here -->
<!-- scriv-end-here -->

## [Unreleased]

Entries written since #81 are one file each in [`changelog.d/`](changelog.d/),
and are folded in above when a version is cut. What follows predates that and
stays where it is.

### Added

- **`install.sh` — installing trellis is one command, PATH included.**

  ```
  curl -LsSf https://raw.githubusercontent.com/looseleafgallery/trellis/main/install.sh | sh
  ```

  Installing was friction on every new machine, and the last step was the one
  that bit. `uv tool install` settles which Python, then leaves `~/.local/bin`
  on the reader to put on PATH — so the documented install could finish and
  leave `trellis: command not found` behind it. An install that ends with the
  command not resolving has not finished.

  The script installs with uv, runs `uv tool update-shell` for the PATH half,
  and then **verifies by running the command**, which is the point of it rather
  than a closing flourish. It executes `trellis --version` from the path it
  installed to — not `command -v`, because being on PATH is a weaker claim than
  running, and the run also crosses the version guard and the PyYAML import.
  Then it asks a fresh login shell what `trellis` resolves to. If that is a
  *different* trellis the script says so and names it, because "trellis
  resolves" would otherwise be true and useless. Reporting success while the
  command is not runnable is the failure CONTRIBUTING records as "green output
  is not a green run", and it is the specific thing this script is built not to
  do.

  It never installs a package manager silently: if `uv` is missing it says what
  it is about to install, from where, and into which directory, and
  `--no-install-uv` refuses. It names the profile file it edited rather than
  editing one behind your back, and finds that file by checksumming the
  candidates before and after rather than guessing which one uv picked — uv
  chose `.zshenv` under zsh, where the guess would have been `.zshrc`.
  Idempotent: re-running upgrades to current `main` and does not append a
  second PATH line. It prints its own uninstall line, which names
  `trellis-kernel`, because that is the distribution and `uv tool uninstall
  trellis` errors.

  POSIX `sh`, not bash, since the published line pipes it to `sh`.

- **`reconcile` walks the unconfirmed edges most load-bearing first.** It
  walked them in whatever order they came out of the graph, which answers
  *which edge should I check first* by accident. On the first real graph two of
  seven unconfirmed edges were wrong and either would have been reached fifth.
  Each edge is now offered with two counts and the nodes they came from: how
  many nodes' derivation reads through it, and how many derive differently if
  it is not real. So the sentence a reader ends up with is *this edge is
  `inferred`, never confirmed, **and** more depends on it than any other* -
  which is the pair of facts worth acting on, where either alone is not.

  Counts, not a score, for the same reason `trust` reports counts and never a
  rate: a fragility of `0.73` cannot be checked, and the derivation is the part
  that lets someone disagree with it.

  Ordering is by what reads through the edge, with the counterfactual breaking
  ties, and that way round on evidence rather than by preference. Removing one
  requirement can free its source and stops there, because a node's status is
  declared and nothing downstream moves while the source is still not done.
  Measured over three real graphs, 47 edges came out 38 at zero, 8 at one and 1
  at two, while what reads through them spread from 1 to 8. The counterfactual
  is a real signal about one edge and a poor way to order a list of them - on
  its own it would have left four in five tied, which is the arbitrary order it
  was meant to replace.

  Stale verifications keep their own group behind the never-confirmed edges and
  stay oldest-first inside it. An edge nobody has ever checked and one checked a
  while ago are different questions, and the age ordering is a deliberate answer
  to the second.

  `reconcile` was the one command that answered without deriving anything, so it
  worked on a graph with a cycle in it - which is exactly a graph whose edges are
  worth checking. A cycle now costs the ordering and not the command: the walk
  says which it lost and carries on.

- `queries.edge_sensitivity()` - what rests on one edge, as `chokepoints` one
  level down. Removes the edge, re-derives against the same cache, and reports
  the nodes that derive differently alongside the nodes that read through it.
  Two numbers rather than one, and the same warning as `blocking`: they answer
  different questions and quoting the second as the first misreports the graph.

  The removal is expression surgery, exposed as `expr.without_references()`.
  What comes out is the smallest *boolean term* holding the reference, not the
  reference itself - `contract.x.version >= 2` asks nothing once `contract.x` is
  gone, and putting a literal where the reference was would change the question
  rather than withdraw it. A gate left with no requirement goes with it, an
  argument to `all_done` goes, and `at_least(2, a.done)` losing `a` becomes no
  gate rather than `at_least(2)`, which could never open. Where one term names
  two nodes and cannot lose one alone, the other is reported rather than
  quietly folded into the count.

  **`gates` is not in `Delta.EDITABLE_FIELDS` and this does not put it there.**
  The counterfactual rewrites a `gates:` block, which is exactly what the write
  path refuses to do, and that is the point rather than an obstacle: research
  may not loosen execution, so the answer is a read-only surface and never an
  exemption in the write path. `with_overlay` has never carried the writer's
  restriction and already re-derives correctly from a hypothetical gate. The
  state it produces is one no write could reach and is never handed to the
  write loop. See `docs/BOUNDARY.md`.

  An edge the surgery cannot lift - one named by a published fact, which is a
  value the node computes rather than a requirement it carries - is reported as
  unmeasured with the fact that named it, and sorts last. A number that was
  never computed reads as zero unless something says otherwise.

- **An acknowledgement can carry its reason in the graph.** `acknowledge` now
  takes `{code, why}` entries as well as bare codes, and the two can be mixed
  in one list:

  ```yaml
  acknowledge:
    - code: inert_node
      why: spike only, one ticket, nothing gates on it yet
  ```

  Schema change, and not a purely additive one. A list of bare codes is
  unaffected and both forms silence the same finding, but a mapping entry
  inside `acknowledge` is now *validated* where it used to be stringified.
  Only `{code, why}` is accepted: an entry with an unknown key, with no
  `code`, or with a `code` that is not a single scalar is refused by
  `load_graph`. Every one of those previously loaded, as a code like
  `"{'reason': 'spike only'}"` - which silenced nothing and then reported
  itself as a `dead_acknowledgement`. So `acknowledge: [{reason: ...}]` moves
  from silently-broken-but-loading to refusing to load, and a graph carrying
  that typo must fix the key before it will load at all. That is a fix and it
  is deliberate, but it is a graph that used to load and now does not, which
  is the part to know before upgrading. Until now the reason could only be
  written by `review`, which is interactive, while the acknowledgement itself
  lived in the YAML - so `check` asked for something a graph maintained by
  automation had no way to give it, and the diagnostic would have stayed on
  those nodes permanently. Splitting a record from its reason across two files
  is what created the gap; the reason now sits next to the thing it explains,
  and survives the journal being lost. Where both hold one, the declared
  reason is shown - it is the current statement, and editable in place.
  `review` will not append to a list that carries reasons, and says so rather
  than splitting one on the commas inside it.

- `Graph.structure_hash()` - a content address of the graph's *shape*, so a
  negative result can expire by content rather than by calendar. An edge exists
  only because an expression names a node, so only a node appearing, a gate
  changing or a `satisfied_by` changing can create one; statuses, notes and
  evidence cannot, however much they move. That makes *"I looked for
  relationships to this and found none"* a durable fact rather than a note that
  rots: if the hash has not moved, the search still holds by construction.
  Kernel-level, because it is a pure function of the declaration with no clock
  in it - the derived-state hash would have expired a search on every status
  change, for reasons that could not have affected it.
- `trellis brief` - the operating manual, for an agent starting on a graph it
  did not build. `AGENTS.md` now ships with the package, so an agent working
  in someone else's repository no longer has to go and read the trellis source
  to learn the grammar. Prints three lines about the graph in front of it
  first, and still prints the manual when the graph will not load, because
  that is when someone most needs it.
- **`acknowledge` now requires a reason.** Blank has too many readings -
  obvious, unknown, in a hurry, disagreed but moved on - and a reader has to
  guess which. `reject` already refused without one; an acknowledgement is
  permanent and a rejection is not, so the argument is stronger here. The
  prompt now names the test as well as asking the question: *the fact that
  makes this permanently true*. Declining leaves the finding open rather than
  answering it with a blank.
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

- **The corroborator contract says a corroborator may check structure, and
  shows it.** `docs/BOUNDARY.md` defined the interface correctly and then gave
  a single example of a finding — a status disagreement — so a corroborator
  written from it compared statuses and not relations. One reported zero
  conflicts across 26 rows while the graph was missing an edge the tracker had
  held since the ticket was written. A wrong status is one node reporting
  wrong; a missing edge corrupts everything derived from it, which is most of
  what this engine does. The page was teaching the cheaper half of the job as
  though it were the job.

  A second, structural example now sits beside the status one, and the
  definition says structure is in scope rather than leaving it to be inferred
  from an example. Two rules that only surface once someone writes the
  structural check are recorded with it. The comparison is **not symmetric**:
  an edge in the record and not in the graph is a finding, an edge in the graph
  and not in the record usually is not, because trellis models gates a tracker
  cannot express and a set difference taken both ways reports every one of
  them. And a clean result must say what it compared, so a run ends with `26
  rows, status only — relations unchecked` rather than a bare count — which is
  `CONTRIBUTING.md`'s "state what was checked" applied to a corroborator's own
  summary line. The README section carries the second example and points at
  both rules.

  Documentation only. The relations check itself is TRE-3.

- **README §Try it is split by audience, and no longer opens with `pip`.**
  "Use it" is the one-line installer; "work on it" links `CONTRIBUTING.md`
  rather than repeating the recipe. The two jobs were sharing one set of
  instructions, so someone who only wanted to run `trellis state` would follow
  the contributor path and build a virtualenv per checkout. Leading with `pip
  install git+…` never said *which* Python, so it landed wherever `pip`
  pointed.

  `uv tool install`, `pipx` and `pip` stay documented as manual fallbacks for
  anyone who will not pipe a script to `sh`. The macOS system-Python-3.9
  warning moved to sit with the `pip` line, which is the only route it applies
  to: uv chooses an interpreter against `requires-python` and fetches one if
  the system has none. The runtime guard in `trellis/__init__.py` is unchanged
  — it is a backstop, not documentation. The docs now also say that the git URL
  is deliberate pending the publish under `trellis-kernel`, rather than leaving
  it to read as something nobody got round to.

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

- `reconcile` said "all N annotated edges are confirmed" while counting
  `stated` edges, which nobody checked - someone asserted them and they were
  written down. The two-bucket fold behind that count is right, and unchanged:
  the question is whether an edge came from outside the modeller's head, so
  `verified` and `stated` go together and `inferred` and `assumed` go together.
  `confirmed` was just the wrong name for the half that contains `stated`, and
  it reported a graph as finished at the moment its unchecked edges were the
  only ones left to promote. It now names the counting rule in the answer -
  `nothing to check - 24 verified, 3 stated; none inferred or assumed` - so the
  remaining risk is visible without reconstructing where the fold sits. Edges
  carrying no `evidence:` at all are now said to be uncounted rather than left
  implied by the word "annotated".

- `snapshot` decides whether anything changed from the **whole payload**, not
  from the derived state inside it. `refs` ships in the payload and is what a
  corroborator joins on, but it is outside the fingerprint by design - it
  cannot change a derived value, so it must not invalidate a cache entry. The
  result was that thirty-one nodes could be migrated onto `ref:` and `snapshot`
  would answer "nothing has changed", leaving an empty index in the file every
  external checker reads. One did: it joined on that index, missed every
  lookup, and reported sixty-one tickets as unmodelled - a confident, plausible
  wrong answer from a stale field the tool had declined to refresh. The payload
  is the plugin contract, so anything in it is something a consumer can be
  wrong about. Everything it pins now gates the write; `meta` and `trust` do
  not, because they carry the timestamp and today's git history and would make
  every run a new snapshot. The index records the hash that was compared, and
  an entry written before it is read from its stored payload rather than
  assumed to match. The skip message now says what it checked.
- A snapshot id is addressed by what it pins rather than by derived state
  alone. The stamp is second-precision, so two snapshots differing only in
  `refs` shared a directory and the second overwrote the first - the one thing
  the module promises never to do. `state_hash` is unchanged in both the
  payload and the index. The id keeps its `stamp-8hex` shape and existing
  snapshots are not renamed, so an id already written still resolves and still
  lists; only ids taken after this change are derived from the payload hash.
- A corroborator finding about no single node is labelled `(graph)` instead of
  rendering as an empty column and a stray colon. A count across the whole tree
  is a real finding, not a finding missing its node, and the kernel already
  labels its own graph-level findings this way.
- **A node written in YAML flow style is refused at load**, naming the file and
  the line. `{id: a, status: done}` loaded, validated and evaluated perfectly,
  and every write against it failed: the writer rewrites one field's line, and
  a node inside `{...}` has no line of its own. The failure was safe - exit 2,
  nothing written, file untouched - but it arrived at `set` or `accept`, after
  the person had already decided, and flow style is what anything dumping a
  dict emits by default. A graph bootstrapped programmatically therefore read
  perfectly and was permanently read-only, discovered on the first write.
  Teaching the writer to edit flow style is the larger answer and was
  deliberately not taken. Only the node's own mapping is judged: a flow value
  inside a block node, like `evidence: {how: verified, at: 2026-08-20}`, still
  loads and still writes, and the shipped example uses one.

  **This refuses a graph that previously loaded, and the refusal is total.**
  Before, a flow-style graph loaded and could be read: `check`, `explain` and
  `reconcile` all worked, and only `set` and `accept` failed. After, none of
  them work - `load_graph` refuses first, so every command exits 2 and a
  flow-style graph cannot be inspected at all until it is rewritten in block
  style. That is a capability removal and not only a fix, and it is the part
  to know before upgrading. It is taken deliberately: the node could never be
  written to, and failing at the door with the file and the line named beats
  failing at `set` after the person has already decided. Nothing migrates an
  existing graph; the diagnostic names the line and the edit is the author's.
- The corroborator severity clamp emitted a double full stop - "which only the
  kernel can establish.. This is silence, not agreement" - because the detail
  it quotes is itself a diagnostic that ends in one.
- `pip install -e '.[dev]'` now installs `ruff`, so the second of the two
  commands CONTRIBUTING and CLAUDE.md give you can actually be run. It was in
  no dependency list, so the documented lint step was `No such file or
  directory` from a clean checkout — exit 127, and the `&&` after it meant
  `ruff format --check` never ran either. CI never caught it because CI is the
  one place that does not follow those instructions: it installs ruff from an
  action rather than from this project's metadata, so the gate was green
  everywhere except where a contributor stands. Pinned to the exact version
  `.pre-commit-config.yaml` already pinned, and a test now fails if the two
  numbers drift or if the docs name a `.venv/bin` tool nothing installs.
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
