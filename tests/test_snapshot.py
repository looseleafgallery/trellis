"""Point-in-time captures, and the renderer interface around them."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trellis import cli, snapshot
from trellis.engine import Engine
from trellis.loader import load_graph

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


@pytest.fixture
def project(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for src in EXAMPLE.glob("*.yaml"):
        (graph_dir / src.name).write_text(src.read_text())
    return graph_dir


def take(graph_dir, **kw):
    engine = Engine(load_graph(graph_dir))
    return snapshot.take(graph_dir, engine, **kw)


# -- capture -----------------------------------------------------------------


def test_a_snapshot_captures_derived_state_not_the_source(project):
    engine = Engine(load_graph(project))
    payload = snapshot.capture(project, engine)

    # the thing git cannot give you back later
    assert payload["nodes"]["agent.tool_exec"]["readiness"] == "blocked"
    assert "trust" in payload and "findings" in payload
    assert payload["meta"]["nodes"] == 13


def test_the_snapshot_records_when_it_was_true(project):
    payload = snapshot.capture(project, Engine(load_graph(project)))
    taken = datetime.fromisoformat(payload["meta"]["taken_at"])
    assert abs((datetime.now(UTC) - taken).total_seconds()) < 60


def test_identical_state_produces_the_same_address(project):
    a = snapshot.capture(project, Engine(load_graph(project)))
    b = snapshot.capture(project, Engine(load_graph(project)))
    assert a["meta"]["state_hash"] == b["meta"]["state_hash"]


def test_a_changed_declaration_produces_a_different_address(project):
    before = snapshot.capture(project, Engine(load_graph(project)))
    moved = load_graph(project).with_overlay({"tools.sandbox": {"status": "done"}})
    after = snapshot.capture(project, Engine(moved))
    assert before["meta"]["state_hash"] != after["meta"]["state_hash"]


# -- taking, and not taking --------------------------------------------------


def test_taking_writes_the_snapshot_and_indexes_it(project):
    entry, is_new = take(project)
    assert is_new
    assert (snapshot.snapshot_dir(project) / entry.id / "snapshot.json").exists()
    assert [e.id for e in snapshot.read_index(project)] == [entry.id]


def test_an_unchanged_graph_does_not_produce_a_second_snapshot(project):
    first, _ = take(project)
    second, is_new = take(project)
    assert not is_new
    assert second.id == first.id
    assert len(snapshot.read_index(project)) == 1


def test_force_takes_one_anyway(project):
    take(project)
    _entry, is_new = take(project, force=True)
    assert is_new
    assert len(snapshot.read_index(project)) == 2


def test_a_changed_graph_produces_a_new_snapshot(project):
    first, _ = take(project)
    path = project / "tools.yaml"
    path.write_text(path.read_text().replace("status: in_progress", "status: done", 1))
    second, is_new = take(project)
    assert is_new and second.id != first.id


def test_nothing_is_ever_refreshed_in_place(project):
    """A snapshot that updates itself is not a snapshot."""
    entry, _ = take(project)
    written = (snapshot.snapshot_dir(project) / entry.id / "snapshot.json").read_text()

    path = project / "tools.yaml"
    path.write_text(path.read_text().replace("status: in_progress", "status: done", 1))
    take(project)

    after = (snapshot.snapshot_dir(project) / entry.id / "snapshot.json").read_text()
    assert after == written


def test_age_is_computed_from_when_it_was_taken(project):
    entry, _ = take(project)
    later = datetime.now(UTC) + timedelta(days=9)
    assert entry.age_days(later) == 9


# -- what counts as a change -------------------------------------------------


def give_a_ref(graph_dir, node_id="tools.registry", ref="ENG-1552"):
    """Annotate one node with an external id, changing nothing derived."""
    path = graph_dir / "tools.yaml"
    body = path.read_text()
    anchor = f"  - id: {node_id}\n"
    assert anchor in body, f"fixture no longer declares {node_id}"
    path.write_text(body.replace(anchor, f"{anchor}    ref: {ref}\n", 1))


def stored(graph_dir, snapshot_id):
    path = snapshot.snapshot_dir(graph_dir) / snapshot_id / "snapshot.json"
    return json.loads(path.read_text())


def forget_payload_hash(graph_dir):
    """Rewrite the index as a version before the payload was compared wrote it."""
    path = snapshot.snapshot_dir(graph_dir) / snapshot.INDEX_NAME
    lines = []
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        entry.pop("payload_hash")
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")


def test_a_change_to_refs_alone_is_a_new_snapshot(project):
    """The reported bug: 31 nodes gained a ref and this was called nothing.

    `refs` is in the payload, so a corroborator joins on it. A stored one that
    is empty while the graph has thirty-one does not report a disagreement -
    it reports the whole tree as unmodelled, confidently.
    """
    first, _ = take(project)
    give_a_ref(project)
    second, is_new = take(project)

    assert is_new, "the refs index a consumer joins on had changed"
    assert second.id != first.id
    assert stored(project, first.id)["refs"] == {}
    assert stored(project, second.id)["refs"]["tools.registry"] == "ENG-1552"


def test_the_derived_state_could_not_have_seen_that(project):
    """Why it was invisible, pinned so the reason stays visible.

    `ref` is deliberately outside the fingerprint - it cannot change a derived
    value, so it must not invalidate a cache entry. The state hash therefore
    cannot notice it, and the state hash is what used to gate the write.
    """
    before = snapshot.capture(project, Engine(load_graph(project)))
    give_a_ref(project)
    after = snapshot.capture(project, Engine(load_graph(project)))

    assert before["meta"]["state_hash"] == after["meta"]["state_hash"]
    assert before["refs"] != after["refs"]


def test_a_retitled_node_is_a_new_snapshot_too(project):
    """Same rule, different key: titles ship, so a stale one is a wrong label."""
    take(project)
    path = project / "tools.yaml"
    body = path.read_text()
    assert "title: Tool registry and discovery\n" in body
    path.write_text(body.replace("Tool registry and discovery", "Tool registry"))

    entry, is_new = take(project)
    assert is_new
    assert stored(project, entry.id)["titles"]["tools.registry"] == "Tool registry"


def test_everything_the_payload_pins_gates_it_and_nothing_else_does(project):
    """The general rule, so a key added later is gated the day it ships.

    Whole-payload minus the sections that move on their own: `meta` carries the
    timestamp, and `trust` is read from today's git history. Gating on either
    would mean a new snapshot on every run.
    """
    payload = snapshot.capture(project, Engine(load_graph(project)))
    address = snapshot._payload_hash(payload)

    for section in payload:
        mutated = {**payload, section: "something else"}
        if section in snapshot.LIVE_SECTIONS:
            assert snapshot._payload_hash(mutated) == address, section
        else:
            assert snapshot._payload_hash(mutated) != address, section


def test_the_index_records_what_was_compared(project):
    entry, _ = take(project)
    line = (snapshot.snapshot_dir(project) / snapshot.INDEX_NAME).read_text()
    assert entry.payload_hash
    assert json.loads(line)["payload_hash"] == entry.payload_hash


def test_an_entry_indexed_before_this_check_is_read_from_its_payload(project):
    """Upgrading must not cost one more stale snapshot."""
    take(project)
    forget_payload_hash(project)
    give_a_ref(project)

    entry, is_new = take(project)
    assert is_new
    assert stored(project, entry.id)["refs"]["tools.registry"] == "ENG-1552"


def test_an_older_entry_with_nothing_changed_is_still_recognised(project):
    first, _ = take(project)
    forget_payload_hash(project)
    second, is_new = take(project)
    assert not is_new and second.id == first.id


def test_a_missing_payload_is_not_evidence_that_nothing_changed(project):
    """An unknown is not a match. Deleted the file, so we cannot know."""
    first, _ = take(project)
    forget_payload_hash(project)
    (snapshot.snapshot_dir(project) / first.id / "snapshot.json").unlink()

    _entry, is_new = take(project)
    assert is_new


# -- renderers ---------------------------------------------------------------


def test_builtin_renderers_need_no_configuration(project):
    entry, _ = take(project, renderers_wanted=["mermaid", "json"])
    names = {a["renderer"] for a in entry.assets}
    assert {"mermaid", "json"} <= names
    body = (snapshot.snapshot_dir(project) / entry.id / "mermaid.mmd").read_text()
    assert body.startswith("flowchart")


def test_every_asset_carries_its_own_as_of(project):
    """An artifact that travels away from the index still has to say when."""
    entry, _ = take(project, renderers_wanted=["mermaid"])
    rendered = next(a for a in entry.assets if a["renderer"] == "mermaid")
    assert rendered["taken_at"] == entry.taken_at


def test_an_external_renderer_receives_the_snapshot_on_stdin(project, tmp_path):
    script = tmp_path / "r.py"
    script.write_text(
        "import json,sys\n"
        "s=json.load(sys.stdin)\n"
        "print(s['meta']['state_hash'], len(s['nodes']))\n"
    )
    (tmp_path / "trellis.toml").write_text(
        f'[renderer.tiny]\ncommand = ["{sys.executable}", "{script}"]\n'
        'extension = "txt"\n'
    )
    entry, _ = take(project, renderers_wanted=["tiny"])
    body = (snapshot.snapshot_dir(project) / entry.id / "tiny.txt").read_text()
    assert entry.state_hash in body
    assert "13" in body


def test_a_renderer_is_never_given_a_path_to_the_graph(project, tmp_path):
    """Read-only is a property of the interface, not a rule to be obeyed."""
    script = tmp_path / "r.py"
    script.write_text("import json,sys\njson.load(sys.stdin)\nprint('ok')\n")
    (tmp_path / "trellis.toml").write_text(
        f'[renderer.tiny]\ncommand = ["{sys.executable}", "{script}"]\n'
    )
    engine = Engine(load_graph(project))
    payload = snapshot.capture(project, engine)
    renderers = snapshot.load_renderers(project)
    body, _ext = snapshot.render("tiny", payload, engine, renderers)
    assert body.strip() == b"ok"
    # The real property: nothing it is handed says where the graph lives, so it
    # could not reach the source even if it tried.
    serialised = json.dumps(payload)
    assert str(project) not in serialised
    assert str(project.parent) not in serialised


def test_a_failing_renderer_reports_its_stderr(project, tmp_path):
    script = tmp_path / "r.py"
    script.write_text("import sys\nsys.stderr.write('it broke')\nsys.exit(3)\n")
    (tmp_path / "trellis.toml").write_text(
        f'[renderer.bad]\ncommand = ["{sys.executable}", "{script}"]\n'
    )
    with pytest.raises(snapshot.SnapshotError, match="exited 3: it broke"):
        take(project, renderers_wanted=["bad"])


def test_a_missing_renderer_program_is_named(project, tmp_path):
    (tmp_path / "trellis.toml").write_text(
        '[renderer.gone]\ncommand = ["definitely-not-a-real-program"]\n'
    )
    with pytest.raises(snapshot.SnapshotError, match="not found"):
        take(project, renderers_wanted=["gone"])


def test_an_unknown_renderer_fails_before_anything_is_written(project):
    with pytest.raises(snapshot.SnapshotError, match="unknown renderer"):
        take(project, renderers_wanted=["nope"])
    assert snapshot.read_index(project) == []


def test_an_unknown_renderer_fails_even_when_state_is_unchanged(project):
    """Dedupe must not swallow a request that could never be satisfied."""
    take(project)
    with pytest.raises(snapshot.SnapshotError, match="unknown renderer"):
        take(project, renderers_wanted=["nope"])


def test_asking_for_a_missing_artifact_on_an_unchanged_graph_says_so(project):
    take(project)
    with pytest.raises(snapshot.SnapshotError, match="no mermaid artifact"):
        take(project, renderers_wanted=["mermaid"])


def test_a_builtin_name_cannot_be_shadowed(project, tmp_path):
    (tmp_path / "trellis.toml").write_text('[renderer.mermaid]\ncommand = ["x"]\n')
    with pytest.raises(snapshot.SnapshotError, match="built-in"):
        snapshot.load_renderers(project)


def test_a_renderer_without_a_command_is_rejected(project, tmp_path):
    (tmp_path / "trellis.toml").write_text('[renderer.broken]\nextension = "md"\n')
    with pytest.raises(snapshot.SnapshotError, match="needs command"):
        snapshot.load_renderers(project)


def test_no_config_file_is_fine(project):
    assert snapshot.load_renderers(project) == {}


# -- the command -------------------------------------------------------------


def test_cli_take_and_list(project, capsys):
    assert cli.main(["--graph", str(project), "snapshot", "-m", "why"]) == 0
    capsys.readouterr()

    assert cli.main(["--graph", str(project), "snapshot", "--list"]) == 0
    out = capsys.readouterr().out
    assert "0d ago" in out  # age leads
    assert "why" in out
    assert "nothing refreshes them" in out


def test_cli_says_it_is_frozen(project, capsys):
    cli.main(["--graph", str(project), "snapshot"])
    out = capsys.readouterr().out
    assert "it is a snapshot" in out
    assert "will not update" in out


def test_cli_says_what_it_compared_before_skipping(project, capsys):
    """A skip is a claim about the graph, so it has to say what it checked."""
    cli.main(["--graph", str(project), "snapshot"])
    capsys.readouterr()

    assert cli.main(["--graph", str(project), "snapshot"]) == 0
    out = capsys.readouterr().out
    assert "nothing has changed since" in out
    assert "the payload is identical" in out
    assert "trust layer, which read live" in out
    assert "use --force to take one anyway" in out


def test_cli_takes_one_when_only_a_ref_moved(project, capsys):
    cli.main(["--graph", str(project), "snapshot"])
    capsys.readouterr()
    give_a_ref(project)

    assert cli.main(["--graph", str(project), "snapshot"]) == 0
    out = capsys.readouterr().out
    assert "nothing has changed" not in out
    assert "frozen at" in out


def test_cli_reports_an_unknown_renderer(project, capsys):
    assert cli.main(["--graph", str(project), "snapshot", "--render", "nope"]) == 2
    assert "unknown renderer" in capsys.readouterr().err


def test_cli_list_with_no_snapshots(project, capsys):
    assert cli.main(["--graph", str(project), "snapshot", "--list"]) == 0
    assert "no snapshots yet" in capsys.readouterr().out
