"""Drift covers every field the loop writes, and accepting it is one field at a time.

Before #100 drift compared `status` only, so a hand edit to a contract's
`version` - the edit that changes what a contract means - was invisible, and
`drift --accept` took every drifted node at once with the reason optional.
"""

import json
from pathlib import Path

import pytest

from trellis import cli, journal
from trellis.loader import load_graph

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


@pytest.fixture
def workspace(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for src in EXAMPLE.glob("*.yaml"):
        (graph_dir / src.name).write_text(src.read_text())
    return graph_dir


def hand_set(graph_dir: Path, node: str, field: str, value) -> None:
    """Edit one field of one node in its file, the way a person would.

    `None` deletes the line. A field the node does not declare is added under
    its `id`. Asserts it found the node, because an edit that matches nothing
    fails silently (CONTRIBUTING.md).
    """
    for path in graph_dir.glob("*.yaml"):
        lines = path.read_text().splitlines()
        try:
            start = next(
                i for i, ln in enumerate(lines) if ln.strip() == f"- id: {node}"
            )
        except StopIteration:
            continue
        indent = " " * (len(lines[start]) - len(lines[start].lstrip()) + 2)
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if lines[i].strip().startswith("- id:")
            ),
            len(lines),
        )
        for i in range(start + 1, end):
            if lines[i].strip().startswith(f"{field}:"):
                if value is None:
                    del lines[i]
                else:
                    lines[i] = f"{indent}{field}: {value}"
                break
        else:
            assert value is not None, f"{node} has no {field} line to delete"
            lines.insert(start + 1, f"{indent}{field}: {value}")
        path.write_text("\n".join(lines) + "\n")
        return
    raise AssertionError(f"{node} is not in any file under {graph_dir}")


def trellis(graph_dir, *argv) -> int:
    return cli.main(["--graph", str(graph_dir), *argv])


def drifted(graph_dir):
    return journal.drift(graph_dir, load_graph(graph_dir))


# -- A1: every field trellis has written --------------------------------------


@pytest.mark.parametrize(
    ("node", "field", "written", "by_hand"),
    [
        ("tools.sandbox", "status", "done", "in_progress"),
        ("contract.stage_handoff", "version", 2, 3),
        ("tools.sandbox", "title", "Sandbox, by trellis", "Sandbox, by hand"),
        ("tools.sandbox", "parent", "agent", "tools"),
        ("tools.sandbox", "awaiting", "which runtime", "which runtime, by hand"),
        ("tools.sandbox", "ref", "ENG-1", "ENG-2"),
    ],
)
def test_a_hand_edit_to_any_written_field_is_drift(
    workspace, node, field, written, by_hand
):
    assert trellis(workspace, "set", node, f"{field}={written}", "-y") == 0
    hand_set(workspace, node, field, by_hand)

    found = drifted(workspace)
    assert [(d.node, d.field) for d in found] == [(node, field)]
    assert found[0].journaled == written
    assert found[0].actual == by_hand


def test_a_field_trellis_never_wrote_is_not_compared(workspace):
    """Not being managed by the tool is not disagreeing with it - per field."""
    trellis(workspace, "set", "tools.sandbox", "status=done", "-y")
    hand_set(workspace, "tools.sandbox", "title", "renamed by hand")
    assert drifted(workspace) == []


# -- A2: a deleted field ------------------------------------------------------


def test_a_field_deleted_by_hand_is_drift(workspace, capsys):
    trellis(workspace, "set", "tools.sandbox", "ref=ENG-1", "-y")
    hand_set(workspace, "tools.sandbox", "ref", None)

    found = drifted(workspace)
    assert [(d.node, d.field) for d in found] == [("tools.sandbox", "ref")]
    capsys.readouterr()
    trellis(workspace, "drift")
    assert "tools.sandbox.ref: trellis wrote 'ENG-1', file has none" in (
        capsys.readouterr().out
    )


def test_a_field_trellis_cleared_and_the_file_omits_is_not_drift(workspace):
    """`awaiting=none` writes an empty value; a file without the line agrees."""
    trellis(workspace, "set", "tools.sandbox", "awaiting=which runtime", "-y")
    trellis(workspace, "set", "tools.sandbox", "awaiting=none", "-y")
    hand_set(workspace, "tools.sandbox", "awaiting", None)
    assert drifted(workspace) == []


# -- A3: each record names its field -------------------------------------------


def test_each_drift_record_names_its_field(workspace, capsys):
    trellis(workspace, "set", "tools.sandbox", "status=done", "title=Sandbox", "-y")
    hand_set(workspace, "tools.sandbox", "title", "Sandbox, by hand")
    capsys.readouterr()

    assert trellis(workspace, "drift") == 1
    out = capsys.readouterr().out
    assert (
        "tools.sandbox.title: trellis wrote 'Sandbox', file says 'Sandbox, by hand'"
        in out
    )


def test_drift_json_rows_are_shaped_with_their_field(workspace, capsys):
    trellis(workspace, "set", "tools.sandbox", "status=done", "-y")
    hand_set(workspace, "tools.sandbox", "status", "in_progress")
    capsys.readouterr()

    trellis(workspace, "--json", "drift")
    rows = json.loads(capsys.readouterr().out)
    assert [set(row) for row in rows] == [
        {"node", "field", "journaled", "actual", "at", "is_correction"}
    ]
    assert rows[0]["field"] == "status"


# -- A4: which moves are corrections -------------------------------------------


def test_a_version_moved_down_by_hand_is_a_correction(workspace):
    trellis(workspace, "set", "contract.stage_handoff", "version=3", "-y")
    hand_set(workspace, "contract.stage_handoff", "version", 2)
    assert drifted(workspace)[0].is_correction


def test_a_version_moved_up_by_hand_is_not_a_correction(workspace):
    trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y")
    hand_set(workspace, "contract.stage_handoff", "version", 3)
    assert not drifted(workspace)[0].is_correction


def test_a_version_deleted_by_hand_is_a_correction(workspace):
    """A missing version exports as 0, so deleting one moves it down."""
    trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y")
    hand_set(workspace, "contract.stage_handoff", "version", None)
    assert drifted(workspace)[0].is_correction


def test_other_fields_are_plain_changes(workspace):
    trellis(workspace, "set", "tools.sandbox", "title=Sandbox", "-y")
    hand_set(workspace, "tools.sandbox", "title", "A")
    assert not drifted(workspace)[0].is_correction


# -- A5: accepting one field, with a reason ------------------------------------


def two_fields_drifted(workspace):
    trellis(workspace, "set", "tools.sandbox", "status=done", "title=Sandbox", "-y")
    hand_set(workspace, "tools.sandbox", "status", "in_progress")
    hand_set(workspace, "tools.sandbox", "title", "Sandbox, by hand")


def test_accepting_one_field_leaves_the_other_drifting(workspace):
    two_fields_drifted(workspace)
    code = trellis(
        workspace,
        "drift",
        "--accept",
        "tools.sandbox.title",
        "--because",
        "renamed in the design review",
    )
    assert code == 0
    assert [d.target for d in drifted(workspace)] == ["tools.sandbox.status"]
    entry = journal.read(workspace)[-1]
    assert entry["origin"] == "accept"
    assert entry["reason"] == "renamed in the design review"
    assert [(w["node"], w["field"]) for w in entry["writes"]] == [
        ("tools.sandbox", "title")
    ]


def test_bare_accept_is_refused_and_names_the_targets(workspace, capsys):
    two_fields_drifted(workspace)
    entries = len(journal.read(workspace))
    capsys.readouterr()

    code = trellis(workspace, "drift", "--accept", "--because", "both on purpose")
    assert code != 0
    err = capsys.readouterr().err
    assert "name the field to accept" in err
    assert "tools.sandbox.status" in err and "tools.sandbox.title" in err
    assert len(journal.read(workspace)) == entries
    assert len(drifted(workspace)) == 2


def test_accepting_something_that_is_not_drifting_is_refused(workspace, capsys):
    two_fields_drifted(workspace)
    entries = len(journal.read(workspace))
    capsys.readouterr()

    code = trellis(
        workspace, "drift", "--accept", "tools.sandbox.ref", "--because", "x"
    )
    assert code != 0
    assert "tools.sandbox.ref is not drifting" in capsys.readouterr().err
    assert len(journal.read(workspace)) == entries


# -- A6: how doctor ranks it ---------------------------------------------------


def doctor_rows(graph_dir, capsys):
    capsys.readouterr()
    trellis(graph_dir, "--json", "doctor")
    return json.loads(capsys.readouterr().out)


def test_version_drift_on_a_consumed_contract_is_a_warning(workspace, capsys):
    trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y")
    hand_set(workspace, "contract.stage_handoff", "version", 3)

    rows = [
        r
        for r in doctor_rows(workspace, capsys)
        if r["node"] == "contract.stage_handoff" and "outside trellis" in r["message"]
    ]
    assert [r["severity"] for r in rows] == ["warn"]
    assert "agent.emit gate on it" in rows[0]["message"]


def test_version_drift_on_a_contract_nobody_gates_on_is_info(tmp_path, capsys):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "c.yaml").write_text(
        "nodes:\n"
        "  - id: c.lonely\n"
        "    kind: contract\n"
        "    status: proposed\n"
        "    version: 1\n"
    )
    trellis(graph_dir, "set", "c.lonely", "version=2", "-y")
    hand_set(graph_dir, "c.lonely", "version", 3)

    rows = [
        r for r in doctor_rows(graph_dir, capsys) if "outside trellis" in r["message"]
    ]
    assert [r["severity"] for r in rows] == ["info"]


def test_other_field_drift_is_info_and_names_the_field(workspace, capsys):
    trellis(workspace, "set", "tools.sandbox", "title=Sandbox", "-y")
    hand_set(workspace, "tools.sandbox", "title", "Sandbox, by hand")

    rows = [
        r
        for r in doctor_rows(workspace, capsys)
        if r["node"] == "tools.sandbox" and "outside trellis" in r["message"]
    ]
    assert [r["severity"] for r in rows] == ["info"]
    assert rows[0]["message"].startswith("title changed outside trellis")
