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
