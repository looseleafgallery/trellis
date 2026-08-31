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


def test_the_fragment_directory_is_the_one_every_document_names():
    """Three files name where a changelog entry goes; one answer, or none.

    The convention only holds if the place is unambiguous. A contributor who
    reads CONTRIBUTING and a contributor who reads the pull-request checklist
    have to arrive at the same directory as the one scriv writes to, and that
    directory has to exist - scriv refuses to create it.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    configured = pyproject["tool"]["scriv"]["fragment_directory"]

    assert (ROOT / configured).is_dir(), (
        f"pyproject.toml points scriv at {configured!r}, which does not exist. "
        "scriv will not create it."
    )
    for doc in (ROOT / "CONTRIBUTING.md", ROOT / ".github/pull_request_template.md"):
        assert configured in doc.read_text(), (
            f"{doc.name} does not say that an entry goes in {configured!r}"
        )


def test_every_changelog_fragment_carries_a_category_the_changelog_uses():
    """A fragment is collected verbatim, so a wrong heading lands in the file.

    `scriv create` writes every category commented out and expects one to be
    uncommented. A fragment committed unedited is silently empty - it survives
    review looking like an entry and contributes nothing when collected.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scriv = pyproject["tool"]["scriv"]
    categories = set(scriv["categories"])
    fragments = ROOT / scriv["fragment_directory"]

    # scriv's `skip_fragments` default excludes README.*; it is instructions,
    # not an entry.
    for fragment in sorted(fragments.glob("*.md")):
        if fragment.name.startswith("README."):
            continue
        text = fragment.read_text()
        # Only headings outside the comment blocks the template ships with.
        live = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        headings = re.findall(r"^### (.+)$", live, flags=re.MULTILINE)
        assert headings, (
            f"{fragment.name} has no uncommented category heading - it was "
            "committed as `scriv create` wrote it, and collects to nothing"
        )
        unknown = sorted({h.strip() for h in headings} - categories)
        assert not unknown, (
            f"{fragment.name} uses categories the changelog does not: {unknown}"
        )
        assert re.search(r"^- ", live, flags=re.MULTILINE), (
            f"{fragment.name} lists nothing under its heading"
        )


def test_the_changelog_says_where_collected_entries_go():
    """scriv writes between two markers and reads no further than the second.

    Without them `scriv collect` stops on the first heading it cannot read as a
    version, which `## [Unreleased]` is not - so the half of this convention
    that runs at release time would fail the first time anyone tried it.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    level = int(pyproject["tool"]["scriv"]["md_header_level"])

    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.find("scriv-insert-here")
    end = text.find("scriv-end-here")
    assert start != -1, "CHANGELOG.md has no scriv-insert-here marker"
    assert end != -1, "CHANGELOG.md has no scriv-end-here marker"
    assert start < end, "the insert marker has to come before the end marker"

    # Everything scriv parses lies between the markers, and it must be able to
    # read all of it as versions. Leaving [Unreleased] outside is deliberate.
    assert text.find("## [Unreleased]") > end, (
        "the [Unreleased] heading is inside scriv's parse window; it is not a "
        "version and collect will refuse it"
    )
    # scriv writes its version headings at this level, so a mismatch would
    # collect entries in at the wrong depth.
    assert text.count("\n" + "#" * level + " [Unreleased]") == 1, (
        f"md_header_level is {level}, which is not the changelog's own level"
    )


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


def test_ci_lints_with_the_ruff_the_dev_extra_pins():
    """CI is the third place a ruff version can come from, and the loudest.

    The test above keeps the dev extra and pre-commit on one number. Neither
    constrains the lint job, and the job is the one that decides whether a pull
    request is red. An action that resolves its own ruff makes somebody else's
    release the reason a branch fails, over formatting its author cannot
    reproduce in the venv CONTRIBUTING told them to build — and the failure
    lands on whoever pushed next, not on whoever changed anything. So the job
    installs the dev extra and runs what the install gave it.
    """
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text()
    steps = yaml.safe_load(text)["jobs"]["lint"]["steps"]

    fetched = [s["uses"] for s in steps if "ruff" in s.get("uses", "")]
    assert not fetched, (
        f"the lint job fetches ruff from {fetched}, which resolves a version "
        f"this repository does not choose; install the dev extra instead"
    )

    run = "\n".join(s["run"] for s in steps if "run" in s)
    assert re.search(r"pip install\b.*\[dev\]", run), (
        "the lint job does not install the dev extra, so the ruff it judges a "
        "pull request with is not the ruff a contributor's venv has"
    )
    for command in ("ruff check", "ruff format --check"):
        assert command in run, f"the lint job no longer runs `{command}`"

    # The pin is worth having only while it is the sole source of the number.
    # A version written here would be a fourth copy, and the two-places test
    # above would go on passing while CI ran something else entirely.
    own = re.findall(r"ruff[^\n]*?[=@]=?\s*v?\d[\d.]*", text)
    assert not own, f"ci.yml names its own ruff version in {own}"


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


def test_the_installer_uninstall_line_names_the_distribution():
    """uv knows the tool by the distribution name, not by the command name.

    The command, the import and the tool are all `trellis`; the distribution is
    `trellis-kernel`. So the uninstall line the installer prints has to name the
    latter - `uv tool uninstall trellis` exits 2 with "`trellis` is not
    installed", which is a confusing way to find out. TRE-6 may rename the
    distribution, and this is the test that says install.sh is one of the places
    that has to change with it.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    distribution = pyproject["project"]["name"]

    script = (ROOT / "install.sh").read_text()
    assert f"uv tool uninstall {distribution}" in script, (
        f"install.sh does not print `uv tool uninstall {distribution}`; "
        "uv resolves tools by distribution name, so any other name errors"
    )


def test_the_installer_is_posix_sh_because_the_published_line_pipes_to_sh():
    """`curl ... | sh` runs under whatever /bin/sh is, which is often not bash."""
    script = (ROOT / "install.sh").read_text()
    assert script.startswith("#!/bin/sh\n"), (
        "install.sh must declare #!/bin/sh: the documented one-liner pipes it "
        "to `sh`, so a bash shebang would be a lie about what it may use"
    )
    assert "#!/bin/bash" not in script
    assert "#!/usr/bin/env bash" not in script


def test_the_readme_documents_the_installer_that_exists():
    """A documented flag that the script does not parse is an error report.

    The README is the manual, and the installer is reached by copying a line out
    of it. A URL or flag that has drifted from install.sh fails in the reader's
    terminal rather than here.
    """
    readme = (ROOT / "README.md").read_text()
    script = (ROOT / "install.sh").read_text()

    url = "https://raw.githubusercontent.com/looseleafgallery/trellis/main/install.sh"
    assert url in readme, "the README no longer gives the one-line install"

    # Every long option the README shows being passed to the installer has to be
    # one install.sh actually accepts.
    documented = set(re.findall(rf"{re.escape(url)}[^\n]*?(--[a-z][a-z-]*)", readme))
    assert documented, "the README shows no installer options"
    for option in sorted(documented):
        assert f"{option})" in script, (
            f"README passes {option} to install.sh, which does not parse it"
        )


def test_the_installer_offers_the_same_git_url_the_readme_does():
    """One URL, so a fork or a rename cannot fix half of the install paths."""
    url = "git+https://github.com/looseleafgallery/trellis.git"
    assert url in (ROOT / "install.sh").read_text()
    assert url in (ROOT / "README.md").read_text()


# -- the console examples ----------------------------------------------------
#
# The yaml examples above are parsed, and the commands, statuses and exports the
# prose names are checked. Nothing read the blocks that show what a command
# *prints*, and three consecutive rendering changes went by without touching
# them: the README went on showing a header line that had been deleted, a `.`
# gutter glyph that is now reserved for readiness marks, and a review menu of
# five bare keys. A doc that shows output the tool cannot produce is the same
# failure as a doc that shows yaml the loader rejects.


def console_blocks(path: Path) -> list[tuple[int, str]]:
    """Fenced blocks opened with a bare ```, with the line they start on.

    Every fence toggles, tagged or not. Watching only for the bare ones would
    read a ```yaml block's closing fence as an opening one and mistake the
    prose after it for a transcript.
    """
    out = []
    lines = path.read_text().splitlines()
    console = False
    inside = False
    start = 0
    buffer: list[str] = []
    for number, line in enumerate(lines, 1):
        fence = line.strip()
        if not fence.startswith("```"):
            if inside and console:
                buffer.append(line)
            continue
        if inside:
            if console:
                out.append((start, "\n".join(buffer)))
            inside = False
            continue
        inside, console, start, buffer = True, fence == "```", number, []
    return out


def transcript(path: Path, command: str, containing: str) -> str:
    """The block that runs `$ <command>` and mentions `containing`, minus its
    first line.

    Fails rather than returning nothing: a block that has been renamed away is
    exactly the drift this is here to catch, and a test that silently checks
    nothing is worse than no test.
    """
    found = [
        body.split("\n", 1)[1]
        for _line, body in console_blocks(path)
        if body.startswith(f"$ {command}\n") and containing in body
    ]
    assert len(found) == 1, (
        f"{path.name} has {len(found)} `$ {command}` blocks mentioning "
        f"{containing!r}, expected exactly one"
    )
    return found[0]


DOCTOR_EXAMPLE = (
    "nodes:\n"
    "  - id: contract.stage_handoff\n"
    "    kind: contract\n"
    "    status: draft\n"
    "    satisfied_by: [runner]\n"
    "\n"
    "  - id: pipeline\n"
    "    status: in_progress\n"
    "\n"
    "  - id: runner\n"
    "    parent: pipeline\n"
    "    status: not_started\n"
    "\n"
    "  - id: agent.emit\n"
    "    parent: pipeline\n"
    "    status: not_started\n"
    "    gates: {start: contract.stage_handoff.live}\n"
    "    evidence:\n"
    "      contract.stage_handoff: inferred\n"
)


def test_the_doctor_transcript_in_the_readme_is_what_doctor_prints(tmp_path, capsys):
    """The README's `doctor` example, run.

    Pinned exactly rather than sampled for a phrase or two, because what went
    stale here was the *shape* — the severity blocks, the code column and the
    footer — and every phrase-level check would have passed on the old block.
    """
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(DOCTOR_EXAMPLE)

    cli.main(["--graph", str(graph_dir), "doctor"])
    printed = capsys.readouterr().out.strip()

    expected = transcript(ROOT / "README.md", "trellis doctor", "undrafted_contract")
    assert printed == expected.strip(), (
        "the README shows output `trellis doctor` does not produce. It is a "
        "transcript, not an illustration - paste a real run over it."
    )


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_a_documented_clean_run_names_the_scope_it_always_names(path):
    """A truncated scope teaches the opposite of what the scope is for.

    `cmd_doctor` prints the structural line and a corroborator line on every
    clean run - the second either names the corroborators it ran or says there
    were none. A doc that shows a clean result with that line missing is
    showing a narrower claim than the tool ever makes, which is the failure the
    scope block exists to prevent.
    """
    for line, body in console_blocks(path):
        if "nothing looks wrong across" not in body:
            continue
        assert "structure:" in body, (
            f"{path.name}:{line} shows a clean run that does not name what it "
            f"checked structurally"
        )
        assert "outside trellis" in body, (
            f"{path.name}:{line} shows a clean run with no corroborator line; "
            f"`doctor` prints one on every clean run, so this block claims a "
            f"narrower scope than the tool does"
        )


def test_the_review_menu_in_the_readme_is_the_menu_review_prints(monkeypatch):
    """The keys, and what each one says it does to the graph.

    Five bare keys on one line is what this replaced: a verb is not a
    consequence, and `acknowledge` reads like dismissing a notice while being a
    permanent ruling in the YAML. The README kept the old row for two releases.
    """
    from trellis.model import Graph, node_from_dict
    from trellis.queries import Problem
    from trellis.style import PLAIN

    monkeypatch.setenv("EDITOR", "vim")
    graph = Graph(
        {
            "contract.x": node_from_dict(
                {"id": "contract.x", "kind": "contract", "status": "draft"}
            )
        }
    )
    problem = Problem(
        code="undrafted_contract", severity="warn", node="contract.x", message="m"
    )
    menu = cli._full_menu(cli._choices(graph, problem, 0, "contracts.yaml"), PLAIN)

    shown = transcript(ROOT / "README.md", "trellis review", "[node 1/1]")
    assert menu in shown, (
        "the README shows a `review` menu that `_full_menu` does not print"
    )
