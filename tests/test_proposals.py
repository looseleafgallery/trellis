"""Proposals that outlive the session that made them.

The queue exists because an agent models and a person decides, and they are
almost never at the keyboard together. What makes it worth having rather than
dangerous is that accepting one is not a replay: the consequence is recomputed
against the graph as it is now, and a proposal made against a declaration that
has since moved is refused rather than applied to something else.
"""

import json
import sys

import pytest

from trellis import cli, proposals
from trellis.delta import Delta, ProposedChange
from trellis.loader import load_graph

GRAPH = """nodes:
  - id: a
    title: Schema work
    status: in_progress
  - id: b
    title: Consumer
    status: not_started
    gates: {start: a.done}
"""


@pytest.fixture
def workspace(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(GRAPH)
    return graph_dir


@pytest.fixture
def confirmed(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p="": "y")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def run(workspace, *args):
    return cli.main(["--graph", str(workspace), *args])


def test_a_proposal_writes_nothing(workspace, capsys):
    before = (workspace / "g.yaml").read_text()
    assert run(workspace, "set", "a", "status=done", "--propose") == 0
    assert (workspace / "g.yaml").read_text() == before
    assert "queued as p1" in capsys.readouterr().out
    assert len(proposals.pending(workspace)) == 1


def test_it_lives_where_the_deciding_party_can_see_it(workspace):
    """Committed, for the same reason the journal is.

    A proposal awaiting a decision is a handoff between two parties, so it
    cannot live somewhere the other party never sees.
    """
    run(workspace, "set", "a", "status=done", "--propose")
    path = proposals.proposals_path(workspace)
    assert path.exists()
    assert path.parent.name == "history"
    assert ".trellis" not in str(path)


def test_accepting_recomputes_rather_than_replaying(workspace, capsys, confirmed):
    """A preview captured at propose time is the CI badge that was true when
    it ran. What `b` gets is computed against the graph as it is at accept."""
    run(workspace, "set", "a", "status=done", "--propose")
    capsys.readouterr()
    assert run(workspace, "accept", "p1") == 0
    out = capsys.readouterr().out
    assert "unlocks" in out and "b" in out
    assert load_graph(workspace).get("a").status == "done"


def test_a_proposal_against_a_node_that_moved_is_refused(workspace, capsys, confirmed):
    """Identity, not consequence.

    The thing proposed against is not the thing that is there, so applying it
    would answer a question nobody asked.
    """
    run(workspace, "set", "a", "status=done", "--propose")
    run(workspace, "set", "a", "status=abandoned", "-y", "--because", "scrapped")
    capsys.readouterr()

    assert run(workspace, "accept", "p1") == 2
    err = capsys.readouterr().err
    assert "changed since p1 was proposed" in err
    assert "nothing was written" in err
    assert load_graph(workspace).get("a").status == "abandoned"


def test_rewording_prose_does_not_invalidate_a_pending_decision(workspace):
    """The fingerprint is semantic. Losing a queued decision because someone
    tidied a note would be an infuriating way to lose one."""
    run(workspace, "set", "a", "status=done", "--propose")
    path = workspace / "g.yaml"
    path.write_text(path.read_text().replace("title: Schema work", "title: Schema"))

    proposal = proposals.get(workspace, "p1")
    assert proposals.moved(load_graph(workspace), proposal) == []


def test_a_rejection_keeps_its_reason_and_is_recognised_again(workspace, capsys):
    """What stops the same proposal arriving monthly."""
    run(workspace, "set", "a", "status=done", "--propose")
    assert (
        run(workspace, "reject", "p1", "--because", "that was a different branch") == 0
    )
    assert not proposals.pending(workspace)

    capsys.readouterr()
    run(workspace, "set", "a", "status=done", "--propose")
    out = capsys.readouterr().out
    assert "was rejected on" in out
    assert "that was a different branch" in out
    # told, not refused - the same change can be right later
    assert len(proposals.pending(workspace)) == 1


def test_rejecting_without_a_reason_is_refused(workspace, capsys):
    run(workspace, "set", "a", "status=done", "--propose")
    assert run(workspace, "reject", "p1") == 2
    assert "the reason is the point" in capsys.readouterr().err
    assert len(proposals.pending(workspace)) == 1


def test_a_decision_cannot_be_made_twice(workspace, capsys, confirmed):
    run(workspace, "set", "a", "status=done", "--propose")
    run(workspace, "accept", "p1")
    capsys.readouterr()
    assert run(workspace, "accept", "p1") == 2
    assert "already accepted" in capsys.readouterr().err


def test_handles_are_never_reused(workspace):
    """A handle in a three-month-old comment still names the same thing."""
    run(workspace, "set", "a", "status=done", "--propose")
    run(workspace, "reject", "p1", "--because", "no")
    run(workspace, "set", "a", "status=done_unverified", "--propose")
    assert [p.id for p in proposals.all_proposals(workspace)] == ["p1", "p2"]


def test_the_record_is_append_only(workspace):
    """An accept is a later record, never an edit to the proposal."""
    run(workspace, "set", "a", "status=done", "--propose")
    run(workspace, "reject", "p1", "--because", "no")
    kinds = [r["kind"] for r in proposals.read(workspace)]
    assert kinds == ["proposed", "rejected"]


def test_a_stale_proposal_is_challenged(workspace, capsys):
    """A queue nobody empties looks handled, which is worse than the prose."""
    run(workspace, "set", "a", "status=done", "--propose")
    path = proposals.proposals_path(workspace)
    aged = path.read_text().replace(
        proposals.get(workspace, "p1").at, "2020-01-01T00:00:00+00:00"
    )
    path.write_text(aged)

    assert [p.id for p in proposals.stale(workspace)] == ["p1"]
    capsys.readouterr()
    run(workspace, "trust")
    assert "nobody has decided" in capsys.readouterr().out


def test_a_corrupt_line_loses_one_record_not_the_queue(workspace):
    run(workspace, "set", "a", "status=done", "--propose")
    path = proposals.proposals_path(workspace)
    path.write_text("{not json\n" + path.read_text())
    assert len(proposals.pending(workspace)) == 1


def test_the_content_key_ignores_the_handle(tmp_path):
    """Identity is what the change does, so a re-proposal can find its own
    prior rejection."""
    one = Delta(changes=[ProposedChange("a", "status", "done", why="tests pass")])
    two = Delta(changes=[ProposedChange("a", "status", "done", why="different words")])
    assert proposals.content_key(one) == proposals.content_key(two)

    other = Delta(changes=[ProposedChange("a", "status", "abandoned")])
    assert proposals.content_key(one) != proposals.content_key(other)


def test_pending_json_says_what_moved(workspace, capsys):
    run(workspace, "set", "a", "status=done", "--propose")
    run(workspace, "set", "a", "status=abandoned", "-y", "--because", "scrapped")
    capsys.readouterr()
    assert run(workspace, "--json", "pending") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "p1"
    assert payload[0]["moved"] == ["a"]


def test_an_unknown_handle_is_an_error(workspace, capsys):
    assert run(workspace, "accept", "p9") == 2
    assert "no proposal 'p9'" in capsys.readouterr().err


def test_nothing_pending_says_so(workspace, capsys):
    assert run(workspace, "pending") == 0
    assert "nothing pending" in capsys.readouterr().out
