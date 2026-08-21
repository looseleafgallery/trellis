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


# -- a ref that cannot resolve says so --------------------------------------
#
# Two ways to declare a join key that can never work. #46: the value is not one
# scalar, so it keys on a Python repr. #43: the value is well-formed but names
# a node, which always wins. Both used to be silent, which is the failure mode
# that costs more than the feature returns.


def test_a_list_ref_is_refused():
    """#46: it loaded, displayed as ['ENG-1599', 'ENG-1600'], and never
    resolved. A repr validates and compares as a perfectly good string that
    means nothing."""
    with pytest.raises(ModelError, match="must be one value, got a list"):
        node_from_dict({"id": "a", "ref": ["ENG-1599", "ENG-1600"]})


def test_the_refusal_says_what_to_do_instead():
    with pytest.raises(ModelError, match="put the second id in the title"):
        node_from_dict({"id": "a", "ref": ["ENG-1599", "ENG-1600"]})


def test_a_mapping_ref_is_refused():
    with pytest.raises(ModelError, match="must be one value, got a dict"):
        node_from_dict({"id": "a", "ref": {"tracker": "ENG-1599"}})


def test_a_numeric_ref_is_a_ticket_id_not_a_mistake():
    """YAML reads `ref: 1552` as an int, and a bare numeric ticket id is a
    normal thing to write."""
    assert node_from_dict({"id": "a", "ref": 1552}).ref == "1552"


def test_a_numeric_ref_still_resolves(tmp_path, capsys):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text("nodes:\n  - id: a\n    ref: 1552\n")
    assert cli.main(["--graph", str(graph_dir), "state", "1552"]) == 0
    assert "a  (work)" in capsys.readouterr().out


def test_a_boolean_ref_names_the_yaml_trap():
    """`ref: yes` is a bool, the same class as `ref: #39` being a comment."""
    with pytest.raises(ModelError, match="YAML boolean"):
        node_from_dict({"id": "a", "ref": True})


def test_the_whole_free_text_class_is_covered():
    """`ref` is where this became visible because it is the only one of the
    four with a lookup behind it - not because it was the only one wrong."""
    for field in ("ref", "title", "awaiting", "notes"):
        with pytest.raises(ModelError, match="must be one value"):
            node_from_dict({"id": "a", field: ["x", "y"]})


def test_an_absent_title_still_falls_back_to_the_id():
    assert node_from_dict({"id": "a"}).title == "a"


def test_the_refusal_reaches_the_write_path(tmp_path):
    """A model proposing a list ref through `log` is rejected too: validation
    runs the real constructor rather than re-deriving the rules."""
    from trellis import delta as delta_mod

    graph = build({"id": "a", "status": "not_started"})
    problems = delta_mod.validate(
        delta_mod.Delta(
            new_nodes=[{"id": "b", "ref": ["ENG-1", "ENG-2"], "status": "not_started"}]
        ),
        graph,
    )
    assert any("must be one value" in p for p in problems)


def test_a_ref_shadowing_a_node_id_is_a_finding():
    """#43: node ids win, so this ref can never resolve - and the only way to
    find out was to try it and get somebody else's node back."""
    graph = build({"id": "a"}, {"id": "shadow", "ref": "a"})
    problems = [p for p in queries.collect(graph) if p.code == "shadowed_ref"]
    assert [p.node for p in problems] == ["shadow"]
    assert "can never resolve" in problems[0].message


def test_a_shadowed_ref_is_not_an_error():
    """The graph is not broken and the other node is returned correctly. Only
    the join is dead."""
    graph = build({"id": "a"}, {"id": "shadow", "ref": "a"})
    problems = [p for p in queries.collect(graph) if p.code == "shadowed_ref"]
    assert all(p.severity == "info" for p in problems)


def test_an_ordinary_ref_shadows_nothing():
    graph = build({"id": "a"}, {"id": "b", "ref": "ENG-1"})
    assert not [p for p in queries.collect(graph) if p.code == "shadowed_ref"]


def test_reporting_it_does_not_change_who_wins(tmp_path, capsys):
    """Naming the consequence is the fix; letting the ref win would trade away
    the property that makes the rule right."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n  - id: a\n    status: done\n  - id: shadow\n    ref: a\n"
    )
    assert cli.main(["--graph", str(graph_dir), "state", "a"]) == 0
    assert capsys.readouterr().out.startswith("a  (work)")
