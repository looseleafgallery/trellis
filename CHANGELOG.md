# Changelog

Notable changes to trellis. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semver](https://semver.org/spec/v2.0.0.html).

Until 1.0 the graph schema may change between minor versions. Schema changes
will always appear here, because a graph you already wrote is the thing most
expensive to break.

## [Unreleased]

### Added

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

- `AGENTS.md` bootstrapping guidance rewritten around three starting conditions
  (blank page, tracker only, tracker plus documents), which need the interview
  questions in near-opposite proportions. Adds the measured rule that edges
  should come from a system of record, and the reconciliation step that has to
  accompany extracting them from prose.

- Findings are ranked by what to fix first rather than only by severity, and
  `check` ends by naming the one to start with.

### Fixed

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
