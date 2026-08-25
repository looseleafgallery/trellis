# changelog.d

A changelog entry goes in a new file here, not in `CHANGELOG.md`.

    ./.venv/bin/scriv create --edit

Every pull request that edited `CHANGELOG.md` conflicted with every other one
that did, because they all appended to the same list. Nothing else about them
conflicted. A file per change removes the collision rather than making it
easier to resolve, which matters most where the resolution is unattended: two
agents appending to one list always collide, and two agents each writing a
distinct file never can (#81).

The file is ordinary Markdown in the shape `CHANGELOG.md` already uses - a
`### Added` (or `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`)
heading and bullets under it. `scriv collect` groups the fragments by those
headings under one version heading, so an entry is written once and moves into
the changelog verbatim.

`scriv create` names the file from the date, your git nick and the branch. Any
name works - every `*.md` here is collected - but let it choose unless you have
a reason, because what makes the name safe is that it is not one anybody else
would arrive at.

This README is not collected; `skip_fragments` excludes it.

## What is not here

`CHANGELOG.md` keeps every entry written before this directory existed. They
were not migrated: rewriting 480 lines of prose to prove a mechanism works
would be a large diff through the one file this change exists to stop editing,
and the entries are already correct where they are. Fragments accumulate here
alongside them and are folded in at release time.

Cutting that release, and what happens to `[Unreleased]` when it is cut, is
TRE-6 and is deferred on the PyPI distribution-name decision. Fragments do not
wait on it.
