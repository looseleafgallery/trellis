# Working on trellis

This file is for changing trellis's own code. `AGENTS.md` is a different
document for a different job — it tells an agent how to *build a graph with*
trellis, and none of it is guidance about this codebase.

## Current phase

**v0.1.0, pre-1.0.** The graph schema may change between minor versions, and
`CHANGELOG.md` says why that is tolerable: a graph someone already wrote is the
most expensive thing to break, so every schema change is recorded there.

Being pre-1.0 is not licence to churn. It means schema changes are *possible*
and must be argued for and written down — not that they are cheap.

## The invariants are in CONTRIBUTING.md and they are binding

Read `CONTRIBUTING.md` before proposing anything. It lists what this project is
careful about, and each entry exists because something was hard to get back. A
change that breaks one needs an explicit argument, not a passing mention.

The two that most often catch a well-meaning change:

- **The engine is deterministic.** No model calls, no network, in graph
  evaluation. A model may propose; it never computes state and never judges
  whether a gate is satisfied.
- **Evidence challenges; it never sets.** Nothing in the trust layer may change
  a declared value.

## Verifying

    ./.venv/bin/python -m pytest -q
    ./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .

Both, and read the output rather than the exit code. CONTRIBUTING records a
real incident here — "green output is not a green run", where `ruff check` was
piped through `tail -1` and a failure looked like a pass.

**Docs are tested.** Every YAML example in `README.md` and `AGENTS.md` is
parsed by the suite, so changing the schema means changing those examples.

## Scope

The README is explicit about what trellis is not: not a tracker, planner, or
scheduler. No tickets, assignees, dates, estimates, or `owner:` field.
Proposals that reintroduce any of those are out of scope regardless of how
they are framed. Say so and stop rather than building a small version of one.
