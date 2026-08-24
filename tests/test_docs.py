"""The documentation has to stay true.

AGENTS.md is read by agents that will copy its YAML verbatim and write it to
disk. A doc that silently drifts from the schema is worse than no doc, because
it produces confident, wrong files. So every example in it is parsed here, and
every command it names has to exist.
"""

import re
from pathlib import Path

import pytest
import yaml

from trellis import cli
from trellis.model import ModelError, node_from_dict

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "AGENTS.md", ROOT / "README.md"]


def yaml_blocks(path: Path) -> list[tuple[int, str]]:
    """Fenced yaml blocks, with the line they start on."""
    out = []
    lines = path.read_text().splitlines()
    inside = False
    start = 0
    buffer: list[str] = []
    for number, line in enumerate(lines, 1):
        if line.strip() == "```yaml":
            inside, start, buffer = True, number, []
            continue
        if inside and line.strip() == "```":
            out.append((start, "\n".join(buffer)))
            inside = False
            continue
        if inside:
            buffer.append(line)
    return out


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_every_yaml_example_is_a_valid_node(path):
    blocks = yaml_blocks(path)
    assert blocks, f"{path.name} has no yaml examples - did the fences change?"

    checked = 0
    for line, block in blocks:
        payload = yaml.safe_load(block)
        if not isinstance(payload, dict):
            continue
        specs = payload.get("nodes", [payload])
        for spec in specs:
            if not isinstance(spec, dict) or "id" not in spec:
                continue  # a fragment illustrating one field, not a whole node
            try:
                node_from_dict(spec)
            except ModelError as exc:
                pytest.fail(f"{path.name}:{line} does not parse: {exc}")
            checked += 1
    assert checked, f"{path.name} has no complete node examples to check"


def test_agents_md_only_names_commands_that_exist():
    text = (ROOT / "AGENTS.md").read_text()
    named = set(re.findall(r"`trellis ([a-z]+)", text))
    parser = cli.build_parser()
    real = set(
        next(
            action.choices
            for action in parser._subparsers._group_actions
            if action.choices
        )
    )
    assert named <= real, f"AGENTS.md names commands that do not exist: {named - real}"


def test_agents_md_documents_every_status():
    """A status missing from the doc is one an agent will never write."""
    from trellis.model import CONTRACT_STATUSES, WORK_STATUSES

    text = (ROOT / "AGENTS.md").read_text()
    for status in WORK_STATUSES + CONTRACT_STATUSES:
        assert f"`{status}`" in text, f"AGENTS.md never mentions the {status!r} status"


def test_agents_md_documents_every_builtin_export():
    from trellis.model import RESERVED_EXPORTS

    text = (ROOT / "AGENTS.md").read_text()
    # `status` is reserved to keep references unambiguous but is not an export.
    for name in RESERVED_EXPORTS - {"status"}:
        assert f"`{name}`" in text, f"AGENTS.md never mentions the {name!r} fact"


def test_agents_md_names_every_provenance_value():
    from trellis.model import HOW

    text = (ROOT / "AGENTS.md").read_text()
    for how in HOW:
        assert f"`{how}`" in text


def test_version_reports_something_actionable():
    """Installs come from git, so a bare version number would not identify a build."""
    from trellis import __version__
    from trellis.cli import _version

    reported = _version()
    assert __version__ in reported
    assert reported.startswith("trellis ")


def test_readme_gives_the_user_install_not_the_contributor_one():
    text = (ROOT / "README.md").read_text()
    assert "pip install git+https://github.com/looseleafgallery/trellis.git" in text
    # `trellis` on PyPI belongs to an unrelated project; saying so avoids a
    # confusing failure for anyone who tries the obvious thing.
    assert "no `pip install trellis`" in text


def test_the_version_floor_is_stated_in_one_place_and_agreed_everywhere():
    """#10: an old pip ignores requires-python, so the guard must match it."""
    import re

    import trellis

    pyproject = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'requires-python = ">=(\d+)\.(\d+)"', pyproject)
    assert declared, "pyproject.toml no longer declares requires-python"
    floor = (int(declared.group(1)), int(declared.group(2)))

    source = (ROOT / "trellis" / "__init__.py").read_text()
    guard = re.search(r"sys\.version_info < \((\d+), (\d+)\)", source)
    assert guard, "the runtime version guard is gone"
    assert (int(guard.group(1)), int(guard.group(2))) == floor

    readme = (ROOT / "README.md").read_text()
    assert f"{floor[0]}.{floor[1]}" in readme, "README does not state the floor"
    assert trellis.__version__


def test_every_command_is_documented_in_the_readme():
    """The README is the manual: a command absent from it does not exist.

    Two README sections and three changelog entries were silently lost to
    scripted edits whose anchors had moved, and nothing noticed because no test
    read either file. `drift` shipped undocumented for four merges.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text()
    readme = (ROOT / "README.md").read_text()
    parser = cli.build_parser()
    commands = set(
        next(
            action.choices
            for action in parser._subparsers._group_actions
            if action.choices
        )
    )

    # The README is the manual, so every command must appear there. Accepting
    # "changelog or readme" let `drift` ship undocumented: it was in the
    # changelog, so the gap in the manual never failed.
    # Matched anywhere the command is shown, fenced or inline — the question is
    # whether the manual documents it, not how it happens to be marked up.
    def shown(text: str, name: str) -> bool:
        return re.search(rf"trellis {re.escape(name)}\b", text) is not None

    missing_readme = [n for n in sorted(commands) if not shown(readme, n)]
    assert not missing_readme, f"commands missing from README: {missing_readme}"

    assert changelog.strip(), "the changelog is empty"


def test_the_changelog_has_an_unreleased_section_with_content():
    text = (ROOT / "CHANGELOG.md").read_text()
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## ", 1)[0]
    assert unreleased.strip(), "the Unreleased section is empty"
    assert "- " in unreleased, "the Unreleased section lists nothing"


def test_the_documented_install_supplies_every_documented_check():
    """`.[dev]` has to install every tool the setup instructions then run.

    `ruff` was named as the lint gate by both CONTRIBUTING.md and CLAUDE.md and
    was in no dependency list, so `./.venv/bin/ruff check .` after the
    documented install was `No such file or directory` — exit 127, and the
    `&& ruff format --check` after it never ran at all. CI stayed green because
    CI is the one place that does not follow these instructions: it installs
    ruff from an action instead of from this project's metadata. A check nobody
    can run locally is a check that only fails on other people's branches.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = pyproject["project"]["optional-dependencies"]["dev"]
    declared = {re.match(r"[A-Za-z0-9._-]+", spec).group(0).lower() for spec in dev}

    # The venv supplies its own interpreter and installer; nothing else is free.
    supplied = {"python", "python3", "pip"}

    for doc in (ROOT / "CONTRIBUTING.md", ROOT / "CLAUDE.md"):
        text = doc.read_text()
        named = set(re.findall(r"\./\.venv/bin/([A-Za-z0-9._-]+)", text))
        assert named, f"{doc.name} no longer runs anything from .venv/bin"
        missing = sorted(t for t in named - supplied if t.lower() not in declared)
        assert not missing, (
            f"{doc.name} tells you to run {missing} after "
            f"`pip install -e '.[dev]'`, which does not install it"
        )


def test_the_ruff_pin_is_the_same_number_in_both_places():
    """Two pinned versions of one linter is a coin flip on every commit.

    pre-commit fixes a `rev:`; the dev extra fixes a `==`. If they drift, the
    hook that blesses a commit and the command that verifies it are different
    programs, and which one is right depends on which you happened to run.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = pyproject["project"]["optional-dependencies"]["dev"]
    pinned = [s for s in dev if re.match(r"ruff\s*==", s)]
    assert pinned, "the dev extra no longer pins ruff to an exact version"
    version = pinned[0].split("==", 1)[1].strip()

    # Scoped to the ruff repo's own block: the file pins several hooks, and a
    # bare version search would happily match somebody else's `rev:`.
    hooks = (ROOT / ".pre-commit-config.yaml").read_text()
    hooked = re.search(r"astral-sh/ruff-pre-commit\s*\n\s*rev:\s*v?(\S+)", hooks)
    assert hooked, ".pre-commit-config.yaml no longer pins a ruff-pre-commit rev"
    assert hooked.group(1) == version, (
        f"the dev extra pins ruff {version}, pre-commit pins {hooked.group(1)}"
    )


def test_the_packaged_manual_matches_the_one_in_the_repo():
    """`trellis brief` ships AGENTS.md so an agent in another repo can read it.

    Two copies of a document drift, and the drift is invisible: the repo copy
    is the one people review, the packaged copy is the one agents obey. So the
    duplication is allowed and guarded rather than trusted.
    """
    repo = (ROOT / "AGENTS.md").read_text()
    packaged = (ROOT / "trellis" / "manual.md").read_text()
    assert packaged == repo, (
        "trellis/manual.md has drifted from AGENTS.md. "
        "Copy AGENTS.md over it in the same commit - the packaged manual is "
        "what agents in other repositories actually read."
    )
