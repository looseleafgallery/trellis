# Changelog

Notable changes to trellis. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semver](https://semver.org/spec/v2.0.0.html).

Until 1.0 the graph schema may change between minor versions. Schema changes
will always appear here, because a graph you already wrote is the thing most
expensive to break.

## [Unreleased]

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
