### Added

- **An agreed contract changes when a person changes it (#100).** Moving the
  `version` of an `agreed` or `frozen` contract, or walking its status back to
  `draft` or `proposed`, now needs an interactive terminal and a reason. The
  check is the same through `set`, `log`, `accept` of a queued proposal and
  `drift --accept`, so accepting a change is never a way around making it, and
  `--propose` is never refused - it is the route for an agent. A contract
  counts as agreed if its file or the last status trellis wrote for it says
  so, so a hand round trip through `draft` does not lower the check.
  `[loop] agreed_contracts = "because"` in `trellis.toml` lets a reason alone
  be enough.
- **`trellis baseline` gives the guard something to count (#100).** A contract
  written as `agreed` by hand has no status trellis wrote, so the guard had only
  the file, and a hand round trip through `draft` lowered it without trace.
  `baseline` records the current status of every agreed or frozen contract that
  has none, changing no YAML, and `doctor` warns about each one until it has.
  It is only as honest as the moment it runs: a contract already walked back is
  not recorded, and a round trip committed to git is not seen.
- **Drift covers every field `set` writes, not just `status` (#100).** A hand
  edit to `version`, `title`, `parent`, `awaiting` or `ref` used to go unseen;
  the edit that changes what a contract means was exactly the one drift could
  not see. Each record names its field, a field deleted by hand counts, and a
  contract's version moved down is a correction. `doctor` ranks a version
  drift on a contract something gates on as a warning.

### Changed

- **`drift --accept` takes one `<node>.<field>` and requires `--because`
  (#100).** Bare `--accept` used to accept every drifted node at once, with the
  reason optional - so a hand edit followed by an accept left a journal saying
  trellis had written it. It is now refused, naming what is drifting.
- **`set version=N` and status walk-backs on agreed or frozen contracts are
  refused without a terminal by default (#100).** Scripts that made those
  changes need `--propose`, or `[loop] agreed_contracts = "because"` and a
  `--because`.
- **The snapshot payload is version 2.** Each `trust.drift` row now carries
  `field`.
