# Contributing

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
```

Optionally `pre-commit install` to run the fast checks before each commit.

## Changelog entries go in `changelog.d/`, not in `CHANGELOG.md`

    ./.venv/bin/scriv create --edit

That writes a new file under `changelog.d/` with a name nobody else will pick.
Keep the one `### Added` (or `Changed`, `Deprecated`, `Removed`, `Fixed`,
`Security`) heading you need, delete the rest, and write the entry as the
entries in `CHANGELOG.md` are written: what changed, and why it was worth
changing. Name the issue inline, as `(#81)`. `scriv collect` folds the
fragments into `CHANGELOG.md`, grouped under those headings, when a version is
cut.

Do not edit `CHANGELOG.md` directly. Every pull request that did conflicted
with every other one that did, because they all appended to the same list, and
nothing else about them conflicted. A file per change removes the collision
instead of making it cheaper to resolve - which matters most where the resolve
is unattended, since two agents appending to one list always collide and two
agents each writing a distinct file never can. `changelog.d/README.md` has the
longer version, including why `merge=union` was refused.

Entries written before `changelog.d/` existed stay in `CHANGELOG.md` where
they are.

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

## Lessons that cost us something

Working on this repository has produced findings that are not bugs, not
features, and not invariants — just things that were learned expensively and
would otherwise be relearned. They live here because a lesson with no home is
a second place to be wrong, which is the failure this project exists to
prevent.

Add to this when a session teaches you something rather than changes something.
Keep each entry to what was actually observed and what to do instead.

**A scripted edit that matches nothing fails silently.** Three CHANGELOG
entries and two whole README sections were never written, because
`s.replace(old, new)` found no anchor and reported nothing. CI stayed green
throughout — no test read those files. `drift` shipped undocumented across four
merges. *Assert the anchor before replacing, and prefer editing a file you have
just read.*

**A tested document can still be half-tested.** "Docs are tested" was true and
insufficient: the suite parsed every YAML example and checked every command,
status and export the prose named, and read nothing that showed what a command
*prints*. Three rendering changes merged in a row, all green, leaving the
README showing a deleted header line, a gutter glyph now reserved for something
else, and a menu that had been replaced. *When you add a check over a document,
say which half of it you covered.*

**Green output is not a green run.** `ruff check` was passed through `tail -1`,
which showed a trailing "no fixes available" line and hid the error above it.
*Read the whole output of a check, or read its exit code — not its last line.*

**A tool's own diagnostics can be as wrong as a user's declaration.** `trust`
reported "not in git, or never committed" from a lookup that could not have
succeeded; `drift` reported "trellis has not written any status yet" when it
had only not written *where it looked*. Both read as facts about the user's
repository rather than facts about where we looked. *State what was checked. If
two causes are distinguishable, distinguish them; if they are not, say the
cause is unknown.*

**Absence of evidence is reported far too easily.** The above is one instance
of a general shape, and it is worth checking any new message against it before
merging.

**`git` reports "No signature" when it means "cannot verify".** `%G?` returns
`N` without `gpg.ssh.allowedSignersFile`, whether or not a signature exists.
*Check `git cat-file commit <sha>` for a `gpgsig` header before concluding
anything was unsigned.*

**A merged PR's `mergeable` field is meaningless.** GitHub computes it lazily
and stops maintaining it once a PR closes, so it reads `UNKNOWN` forever.
*`git merge-tree --write-tree origin/main <branch>` answers the real question
locally.*

**This repository may not be yours alone.** Uncommitted work belonging to
someone else was carried across three branches by ordinary `git checkout`.
Nothing was lost, but `git add -A` at any point would have swept it into an
unrelated commit. *Check `git status` before switching branches, and never
stage with `-A` when you did not write everything in the tree.*

**Do not override the committer identity.** Commits were made with an email
that was not the one the signing key is bound to, so GitHub reported
`unknown_key` and every commit showed as unverified. The repository's own
config is the source of truth for authorship. *Use plain `git commit`.*

## Tests

New behaviour needs a test that fails without it. Tests that assert on real
output are preferred over tests that assert on internals — several bugs found
during development were in the rendering, not the computation.

The evidence tests build real git repositories with backdated commits. They are
slower than the rest and that is deliberate: mocking git history would test the
mock.
