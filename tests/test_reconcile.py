"""Checking believed edges against the world, and keeping what you found.

The outcome is the whole point. Whether an edge held is not recoverable
afterwards: a confirmed one becomes `verified` in the YAML and a wrong one gets
rewritten or deleted — both structural edits, both unjournaled, and the wrong
case usually deletes the evidence of its own failure.
"""

import sys
from pathlib import Path

import pytest

from trellis import cli, journal

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


@pytest.fixture
def workspace(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for src in EXAMPLE.glob("*.yaml"):
        (graph_dir / src.name).write_text(src.read_text())
    return graph_dir


@pytest.fixture
def answers(monkeypatch):
    def scripted(*responses):
        queue = list(responses)

        def fake_input(_prompt=""):
            if not queue:
                raise EOFError
            return queue.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    return scripted


def test_it_refuses_when_not_a_terminal(workspace, capsys):
    assert cli.main(["--graph", str(workspace), "reconcile"]) == 2
    assert "reconcile is interactive" in capsys.readouterr().err


def test_a_wrong_edge_is_recorded_with_its_reason(workspace, answers, capsys):
    answers("w", "the D3 in that sentence was the other project's", "q")
    assert cli.main(["--graph", str(workspace), "reconcile"]) == 0

    found = journal.outcomes(workspace)
    assert len(found) == 1
    assert found[0].held is False
    assert found[0].reason == "the D3 in that sentence was the other project's"
    assert found[0].source == "agent.emit"


def test_a_held_edge_is_recorded_and_offers_the_annotation(workspace, answers, capsys):
    answers("h", "checked against the tracker", "q")
    cli.main(["--graph", str(workspace), "reconcile"])

    found = journal.outcomes(workspace)
    assert found[0].held is True
    out = capsys.readouterr().out
    # Writing `evidence:` is structural, so it hands over the line rather than
    # guessing at the edit.
    assert "how: verified" in out


def test_skipping_records_nothing(workspace, answers, capsys):
    answers("s", "q")
    cli.main(["--graph", str(workspace), "reconcile"])
    assert journal.outcomes(workspace) == []
    assert "nothing recorded" in capsys.readouterr().out


def test_an_outcome_survives_the_edge_being_deleted(workspace, answers):
    """The wrong case usually deletes the evidence of its own failure."""
    answers("w", "not a real dependency", "q")
    cli.main(["--graph", str(workspace), "reconcile"])

    path = workspace / "agent.yaml"
    path.write_text(path.read_text().replace("contract.stage_handoff: inferred", ""))
    assert "contract.stage_handoff: inferred" not in path.read_text()

    still = journal.outcomes(workspace)
    assert len(still) == 1 and still[0].target == "contract.stage_handoff"


def test_calibration_is_counts_not_a_rate(workspace, answers):
    """2 of 7 is honest; 29% is a claim the sample cannot support."""
    answers("w", "wrong one", "q")
    cli.main(["--graph", str(workspace), "reconcile"])
    checked, wrong = journal.calibration(workspace)
    assert (checked, wrong) == (1, 1)


def test_the_running_total_is_reported(workspace, answers, capsys):
    answers("w", "", "q")
    cli.main(["--graph", str(workspace), "reconcile"])
    out = capsys.readouterr().out
    assert "1 of 1 checked edges were wrong" in out
    assert "%" not in out


def test_a_reconciled_edge_is_not_asked_again(workspace, answers, capsys):
    answers("h", "", "q")
    cli.main(["--graph", str(workspace), "reconcile"])
    capsys.readouterr()

    answers()
    assert cli.main(["--graph", str(workspace), "reconcile"]) == 0
    out = capsys.readouterr().out
    assert "nothing new to check" in out
    assert "1 turned out wrong" not in out


def test_all_walks_them_again(workspace, answers, capsys):
    answers("h", "", "q")
    cli.main(["--graph", str(workspace), "reconcile"])
    capsys.readouterr()

    answers("s", "q")
    cli.main(["--graph", str(workspace), "reconcile", "--all"])
    out = capsys.readouterr().out
    assert "edge(s) to check" in out
    assert "last checked" in out  # it says what it found last time


def test_a_graph_with_no_annotations_says_so(tmp_path, capsys):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: a\n    status: done\n"
        "  - id: b\n    status: not_started\n    gates: {start: a.done}\n"
    )
    assert cli.main(["--graph", str(graph_dir), "reconcile"]) == 0
    assert "none of 1 edges are annotated" in capsys.readouterr().out


def test_a_graph_with_no_edges_says_so(tmp_path, capsys):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text("id: a\nstatus: done\n")
    assert cli.main(["--graph", str(graph_dir), "reconcile"]) == 0
    assert "no gates yet" in capsys.readouterr().out
