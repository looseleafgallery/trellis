# Contributing

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
```

Optionally `pre-commit install` to run the fast checks before each commit.

## What this project is careful about

These are the invariants. A change that breaks one needs to argue for it
explicitly, because each exists to protect something that is hard to get back.

**The engine is deterministic.** Graph evaluation makes no model calls and no
network calls. A model may *propose* a change from prose; it never computes
state, and never judges whether a gate is satisfied. The moment a gate can be
wrong in a way you cannot check, the engine loses the property that makes it
worth having.

**Derived state is a pure function of declared fields plus dependency exports.**
Nothing else may enter it. The cache key is a hash of exactly those inputs, so
a hit is exact by construction. In particular, nothing time-dependent belongs
in the engine — that is why the trust layer sits outside it.

**Evidence challenges; it never sets.** Nothing in the trust layer may change a
status, open a gate, or silently reweight an answer.

**The preview is the thing that gets applied.** `impact` and the write loop
share one overlay object. Do not add a second code path that renders what is
about to happen.

**Docs are tested.** Every YAML example in `README.md` and `AGENTS.md` is parsed
by the suite, and the commands, statuses, exports, and provenance values they
name are checked against the code. If you change the schema, those tests tell
you which prose went stale.

## Tests

New behaviour needs a test that fails without it. Tests that assert on real
output are preferred over tests that assert on internals — several bugs found
during development were in the rendering, not the computation.

The evidence tests build real git repositories with backdated commits. They are
slower than the rest and that is deliberate: mocking git history would test the
mock.
