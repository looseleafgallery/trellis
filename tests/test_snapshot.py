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
    rendered = [a for a in entry.assets if a["renderer"] == "mermaid"][0]
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


def test_cli_reports_an_unknown_renderer(project, capsys):
    assert cli.main(["--graph", str(project), "snapshot", "--render", "nope"]) == 2
    assert "unknown renderer" in capsys.readouterr().err


def test_cli_list_with_no_snapshots(project, capsys):
    assert cli.main(["--graph", str(project), "snapshot", "--list"]) == 0
    assert "no snapshots yet" in capsys.readouterr().out
