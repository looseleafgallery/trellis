"""`ref:` — which external item a node *is*.

Identity, not grounding. Nothing here fetches anything or checks whether a
claim still holds; that is a separate question and a separate feature. What is
tested is that the graph becomes joinable to the system everyone else already
addresses this work by, and that adding the field cannot change what the graph
derives.
"""

import json

import pytest

from trellis import cli, journal, queries
from trellis.cache import Cache
from trellis.engine import Engine
from trellis.loader import load_graph
from trellis.model import Graph, ModelError, node_from_dict


def build(*nodes: dict) -> Graph:
    return Graph({n["id"]: node_from_dict(n) for n in nodes})


@pytest.fixture
def workspace(tmp_path):
    """Two nodes, one carrying a ticket id."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: safety.d1\n"
        "    title: External-gate contract v1\n"
        '    ref: "ENG-1552"\n'
        "    status: in_progress\n"
        "  - id: safety.d2\n"
        "    title: Held-status surfacing\n"
        "    status: not_started\n"
        "    gates:\n"
        "      start: safety.d1.done\n"
    )
    return graph_dir


# -- the field --------------------------------------------------------------


def test_ref_is_parsed_and_kept_verbatim():
    node = node_from_dict({"id": "a", "ref": "ENG-1552"})
    assert node.ref == "ENG-1552"


def test_ref_is_opaque():
    """A ticket id, a URL and a spreadsheet coordinate are all legitimate."""
    for value in ("ENG-1552", "https://tracker/x/1", "Sheet1!B7", "#39"):
        assert node_from_dict({"id": "a", "ref": value}).ref == value


def test_absent_ref_is_empty_not_missing():
    assert node_from_dict({"id": "a"}).ref == ""


def test_the_schema_is_still_strict():
    """`ref` is a field now; `owner` deliberately is not."""
    with pytest.raises(ModelError, match="unknown field"):
        node_from_dict({"id": "a", "owner": "someone"})


def test_ref_does_not_change_the_fingerprint():
    """Knowing a node's ticket number cannot change what the node derives, so
    annotating one must not invalidate a cache entry — the same reason
    provenance is excluded."""
    plain = node_from_dict({"id": "a", "status": "done"})
    joined = node_from_dict({"id": "a", "status": "done", "ref": "ENG-1552"})
    assert plain.fingerprint() == joined.fingerprint()


def test_adding_a_ref_reuses_the_cache(tmp_path):
    cache = Cache.load(tmp_path / "cache.json")
    before = build({"id": "a", "status": "done"})
    Engine(before, cache).all_derived()

    after = build({"id": "a", "status": "done", "ref": "ENG-1552"})
    engine = Engine(after, cache)
    engine.all_derived()
    assert engine.reused and not engine.recomputed


# -- the reverse lookup -----------------------------------------------------


def test_lookup_by_ref():
    graph = build(
        {"id": "safety.d1", "ref": "ENG-1552"},
        {"id": "safety.d2", "ref": "ENG-1600"},
    )
    assert graph.by_ref("ENG-1552") == ("safety.d1",)


def test_unclaimed_ref_finds_nothing():
    assert build({"id": "a"}).by_ref("ENG-1") == ()


def test_refs_are_reported_by_node():
    graph = build({"id": "a", "ref": "ENG-1"}, {"id": "b"})
    assert graph.refs() == {"a": "ENG-1"}


# -- ambiguity is reported, never resolved ----------------------------------


def test_two_nodes_may_share_a_ref():
    """Splitting one ticket across two nodes is a legitimate thing to have
    done, so the graph still loads."""
    graph = build({"id": "a", "ref": "ENG-1"}, {"id": "b", "ref": "ENG-1"})
    assert graph.by_ref("ENG-1") == ("a", "b")


def test_a_shared_ref_is_a_finding():
    graph = build({"id": "a", "ref": "ENG-1"}, {"id": "b", "ref": "ENG-1"})
    problems = [p for p in queries.collect(graph) if p.code == "duplicate_ref"]
    assert {p.node for p in problems} == {"a", "b"}
    assert "declared by b" in next(p.message for p in problems if p.node == "a")


def test_a_shared_ref_is_not_an_error():
    graph = build({"id": "a", "ref": "ENG-1"}, {"id": "b", "ref": "ENG-1"})
    problems = [p for p in queries.collect(graph) if p.code == "duplicate_ref"]
    assert all(p.severity == "info" for p in problems)


def test_a_unique_ref_says_nothing():
    graph = build({"id": "a", "ref": "ENG-1"}, {"id": "b", "ref": "ENG-2"})
    assert not [p for p in queries.collect(graph) if p.code == "duplicate_ref"]


# -- the CLI ----------------------------------------------------------------


def test_a_ref_stands_in_for_a_node_id(workspace, capsys):
    code = cli.main(["--graph", str(workspace), "state", "ENG-1552"])
    assert code == 0
    out = capsys.readouterr().out
    assert "safety.d1" in out
    assert "ref        ENG-1552" in out


def test_a_ref_works_wherever_a_node_id_does(workspace, capsys):
    assert cli.main(["--graph", str(workspace), "explain", "safety.d2"]) == 0
    by_id = capsys.readouterr().out
    assert cli.main(["--graph", str(workspace), "deps", "safety.d2"]) == 0
    capsys.readouterr()
    assert cli.main(["--graph", str(workspace), "blocking", "ENG-1552"]) == 0
    assert "safety.d2" in capsys.readouterr().out
    assert "safety.d1" in by_id


def test_node_ids_win_over_refs(tmp_path, capsys):
    """A ref is only consulted when the token names no node, so declaring one
    can never change what an existing command means."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: a\n"
        "    status: done\n"
        "  - id: b\n"
        "    ref: a\n"
        "    status: not_started\n"
    )
    assert cli.main(["--graph", str(graph_dir), "state", "a"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("a  (work)")


def test_an_ambiguous_ref_resolves_to_nothing(tmp_path, capsys):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        'nodes:\n  - id: a\n    ref: "ENG-1"\n  - id: b\n    ref: "ENG-1"\n'
    )
    assert cli.main(["--graph", str(graph_dir), "state", "ENG-1"]) == 2
    err = capsys.readouterr().err
    assert "declared by 2 nodes" in err
    assert "a, b" in err


def test_an_unknown_token_still_reads_as_an_unknown_node(workspace, capsys):
    assert cli.main(["--graph", str(workspace), "state", "nope"]) == 2
    assert "unknown node 'nope'" in capsys.readouterr().err


# -- carrying it out --------------------------------------------------------


def test_json_carries_the_ref(workspace, capsys):
    cli.main(["--graph", str(workspace), "--json", "state", "safety.d1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ref"] == "ENG-1552"


def test_json_says_null_rather_than_omitting(workspace, capsys):
    """A consumer joining on this wants one shape, not two."""
    cli.main(["--graph", str(workspace), "--json", "state", "safety.d2"])
    assert json.loads(capsys.readouterr().out)["ref"] is None


def test_whole_graph_json_carries_refs(workspace, capsys):
    cli.main(["--graph", str(workspace), "--json", "state"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"]["safety.d1"]["ref"] == "ENG-1552"


def test_ready_json_carries_refs(workspace, capsys):
    cli.main(["--graph", str(workspace), "--json", "ready", "--active"])
    payload = json.loads(capsys.readouterr().out)
    assert {item["id"]: item["ref"] for item in payload} == {"safety.d1": "ENG-1552"}


def test_the_tree_shows_refs_behind_a_flag(workspace, capsys):
    cli.main(["--graph", str(workspace), "state"])
    assert "ENG-1552" not in capsys.readouterr().out
    cli.main(["--graph", str(workspace), "state", "--ref"])
    assert "(ENG-1552)" in capsys.readouterr().out


def test_a_snapshot_stays_joinable(workspace, capsys):
    from trellis import snapshot as snapshot_mod

    graph = load_graph(workspace)
    payload = snapshot_mod.capture(workspace, Engine(graph))
    assert payload["refs"] == {"safety.d1": "ENG-1552"}


# -- writing it -------------------------------------------------------------


def test_a_ref_can_be_attached_through_the_loop(workspace, capsys):
    code = cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "safety.d2",
            "ref=ENG-1600",
            "-y",
            "--because",
            "the ticket exists now",
        ]
    )
    assert code == 0
    assert load_graph(workspace).get("safety.d2").ref == "ENG-1600"


def test_attaching_a_ref_is_journaled(workspace):
    cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "safety.d2",
            "ref=ENG-1600",
            "-y",
            "--because",
            "the ticket exists now",
        ]
    )
    entries = journal.read(workspace)
    written = [w for e in entries for w in e["writes"]]
    assert any(w["field"] == "ref" and w["after"] == "ENG-1600" for w in written)


def test_a_ref_can_be_cleared(workspace):
    cli.main(["--graph", str(workspace), "set", "safety.d1", "ref=none", "-y"])
    assert load_graph(workspace).get("safety.d1").ref == ""
