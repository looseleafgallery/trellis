"""The write path: delta -> validate -> preview -> edit -> journal.

The live model call in propose.py is the one thing not covered here — these
tests stub the client so the mapping, caching, validation, and write behaviour
are deterministic. What a real model returns for a given sentence is a prompt
question, not a code question.
"""

from pathlib import Path

import pytest

from trellis import cli, edit, journal, propose, queries
from trellis import delta as delta_mod
from trellis.cache import Cache
from trellis.engine import Engine
from trellis.loader import load_graph
from trellis.model import Graph, node_from_dict

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


@pytest.fixture
def workspace(tmp_path):
    """A throwaway copy of the example graph, with its own cache directory."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for src in EXAMPLE.glob("*.yaml"):
        (graph_dir / src.name).write_text(src.read_text())
    return graph_dir


def change(node, field, value, **kw):
    return delta_mod.ProposedChange(node, field, value, **kw)


# -- delta validation -------------------------------------------------------


def test_overlay_matches_the_what_if_shape():
    d = delta_mod.Delta(changes=[change("a", "status", "done")])
    assert d.overlay() == {"a": {"status": "done"}}


def test_unknown_node_rejected(workspace):
    graph = load_graph(workspace)
    problems = delta_mod.validate(
        delta_mod.Delta(changes=[change("nope", "status", "done")]), graph
    )
    assert any("no such node" in p for p in problems)


def test_unwritable_field_rejected(workspace):
    graph = load_graph(workspace)
    problems = delta_mod.validate(
        delta_mod.Delta(changes=[change("agent.plan", "gates", "x")]), graph
    )
    assert any("not writable" in p for p in problems)


def test_invalid_status_rejected(workspace):
    graph = load_graph(workspace)
    problems = delta_mod.validate(
        delta_mod.Delta(changes=[change("agent.plan", "status", "shipped")]), graph
    )
    assert problems


def test_version_on_work_node_rejected(workspace):
    graph = load_graph(workspace)
    problems = delta_mod.validate(
        delta_mod.Delta(changes=[change("agent.plan", "version", 2)]), graph
    )
    assert any("only contracts" in p for p in problems)


def test_new_node_needs_a_real_parent(workspace):
    graph = load_graph(workspace)
    spec = {
        "id": "agent.retry",
        "title": "Retry",
        "status": "not_started",
        "parent": "ghost",
    }
    problems = delta_mod.validate(delta_mod.Delta(new_nodes=[spec]), graph)
    assert any("does not exist" in p for p in problems)


def test_new_node_colliding_with_an_existing_id_rejected(workspace):
    graph = load_graph(workspace)
    spec = {"id": "agent.plan", "title": "Planner", "status": "not_started"}
    problems = delta_mod.validate(delta_mod.Delta(new_nodes=[spec]), graph)
    assert any("already exists" in p for p in problems)


def test_version_coerced_from_string(workspace):
    graph = load_graph(workspace)
    d = delta_mod.normalize(
        delta_mod.Delta(changes=[change("contract.tool_schema", "version", "3")])
    )
    assert d.changes[0].value == 3
    assert delta_mod.validate(d, graph) == []


def test_noop_changes_dropped(workspace):
    graph = load_graph(workspace)
    d = delta_mod.Delta(
        changes=[
            change("agent.ingest", "status", "done"),  # already done
            change("agent.plan", "status", "done"),
        ]
    )
    kept = delta_mod.drop_noops(d, graph)
    assert [c.node for c in kept.changes] == ["agent.plan"]


# -- writing ----------------------------------------------------------------


def test_write_touches_only_the_target_line(workspace):
    before = (workspace / "tools.yaml").read_text()
    graph = load_graph(workspace)
    d = delta_mod.Delta(changes=[change("tools.sandbox", "status", "done")])
    edit.apply_delta(workspace, graph, d)

    after = (workspace / "tools.yaml").read_text()
    pairs = zip(before.splitlines(), after.splitlines(), strict=True)
    diff = [(b, a) for b, a in pairs if b != a]
    assert diff == [("    status: in_progress", "    status: done")]
    assert len(before.splitlines()) == len(after.splitlines())


def test_comments_and_block_scalars_survive(workspace):
    graph = load_graph(workspace)
    edit.apply_delta(
        workspace,
        graph,
        delta_mod.Delta(changes=[change("agent.plan", "status", "done")]),
    )
    text = (workspace / "agent.yaml").read_text()
    assert "notes: >" in text
    assert "The finish gate is the interesting one" in text


def test_absent_field_is_inserted(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "n.yaml").write_text("nodes:\n  - id: a\n    title: A thing\n")
    graph = load_graph(graph_dir)
    assert graph.get("a").status == "not_started"

    edit.apply_delta(
        graph_dir, graph, delta_mod.Delta(changes=[change("a", "status", "done")])
    )
    assert load_graph(graph_dir).get("a").status == "done"
    assert "title: A thing" in (graph_dir / "n.yaml").read_text()


def test_single_node_file_without_a_list(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "solo.yaml").write_text("id: solo\ntitle: Solo\nstatus: not_started\n")
    graph = load_graph(graph_dir)
    edit.apply_delta(
        graph_dir, graph, delta_mod.Delta(changes=[change("solo", "status", "done")])
    )
    assert load_graph(graph_dir).get("solo").status == "done"


def test_quoting_applied_where_needed(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "n.yaml").write_text("id: a\ntitle: A\nstatus: not_started\n")
    graph = load_graph(graph_dir)
    edit.apply_delta(
        graph_dir,
        graph,
        delta_mod.Delta(changes=[change("a", "title", "Retry: with backoff")]),
    )
    assert load_graph(graph_dir).get("a").title == "Retry: with backoff"


def test_new_node_written_to_its_own_file(workspace):
    graph = load_graph(workspace)
    spec = {
        "id": "tools.retry",
        "kind": "work",
        "title": "Retry layer",
        "parent": "tools",
        "status": "in_progress",
    }
    results = edit.apply_delta(workspace, graph, delta_mod.Delta(new_nodes=[spec]))
    assert any(r.created for r in results)

    reloaded = load_graph(workspace)
    assert reloaded.get("tools.retry").parent == "tools"
    assert reloaded.get("tools.retry").status == "in_progress"


def test_unwritable_field_refused_at_the_writer_too(workspace):
    graph = load_graph(workspace)
    with pytest.raises(edit.EditError):
        edit.apply_delta(
            workspace,
            graph,
            delta_mod.Delta(changes=[change("agent.plan", "gates", "x")]),
        )


def test_failed_verification_restores_every_file(workspace, monkeypatch):
    original = (workspace / "tools.yaml").read_text()
    graph = load_graph(workspace)

    def boom(*_args, **_kwargs):
        raise edit.EditError("simulated verification failure")

    monkeypatch.setattr(edit, "_verify", boom)
    with pytest.raises(edit.EditError):
        edit.apply_delta(
            workspace,
            graph,
            delta_mod.Delta(changes=[change("tools.sandbox", "status", "done")]),
        )
    assert (workspace / "tools.yaml").read_text() == original


def test_failed_write_removes_the_new_node_file(workspace, monkeypatch):
    graph = load_graph(workspace)
    monkeypatch.setattr(
        edit, "_verify", lambda *a, **k: (_ for _ in ()).throw(edit.EditError("no"))
    )
    spec = {
        "id": "tools.retry",
        "title": "Retry",
        "parent": "tools",
        "status": "not_started",
    }
    with pytest.raises(edit.EditError):
        edit.apply_delta(workspace, graph, delta_mod.Delta(new_nodes=[spec]))
    assert not list(workspace.glob("tools.retry*"))


# -- preview ----------------------------------------------------------------


def test_preview_includes_nodes_that_do_not_exist_yet(workspace):
    graph = load_graph(workspace)
    spec = {
        "id": "tools.retry",
        "title": "Retry layer",
        "parent": "tools",
        "status": "not_started",
    }
    result = queries.impact(graph, {}, Cache(), [spec])
    assert result.created == ["tools.retry"]


def test_preview_and_apply_agree(workspace):
    """The overlay previewed is the overlay written — same dict, same engine."""
    graph = load_graph(workspace)
    d = delta_mod.Delta(changes=[change("tools.sandbox", "status", "done")])
    predicted = queries.impact(graph, d.overlay(), Cache(), d.new_nodes)

    edit.apply_delta(workspace, graph, d)
    actual = Engine(load_graph(workspace), Cache()).all_derived()

    for node_id in predicted.unlocked:
        assert actual[node_id].readiness in ("ready", "active")
    for node_id in predicted.contracts_lit:
        assert actual[node_id].readiness == "live"


# -- journal ----------------------------------------------------------------


def test_journal_records_what_and_why(workspace):
    graph = load_graph(workspace)
    d = delta_mod.Delta(changes=[change("tools.sandbox", "status", "done")])
    writes = edit.apply_delta(workspace, graph, d)
    journal.record(workspace, "log", "finished the sandbox", writes, ["something odd"])

    entries = journal.read(workspace)
    assert len(entries) == 1
    assert entries[0]["origin"] == "log"
    assert entries[0]["text"] == "finished the sandbox"
    assert entries[0]["writes"][0]["node"] == "tools.sandbox"
    assert entries[0]["unmatched"] == ["something odd"]


def test_journal_survives_a_corrupt_line(workspace):
    journal.journal_path(workspace).parent.mkdir(parents=True, exist_ok=True)
    journal.journal_path(workspace).write_text(
        '{"at": "x", "origin": "set"}\nnot json\n'
    )
    assert len(journal.read(workspace)) == 1


# -- proposing (stubbed model) ----------------------------------------------


class _StubResponse:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"


class _StubClient:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = 0
        self.messages = self

    def parse(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return _StubResponse(self.parsed)


def _stub(monkeypatch, parsed):
    client = _StubClient(parsed)
    monkeypatch.setattr(propose, "_client", lambda: client)
    return client


def test_proposal_becomes_a_validated_delta(workspace, monkeypatch):
    graph = load_graph(workspace)
    parsed = propose._Proposal(
        changes=[
            propose._Change(
                node="tools.sandbox",
                field="status",
                value="done",
                why="finished the sandbox work",
                confidence=0.95,
            )
        ],
        unmatched=["something about dashboards"],
    )
    _stub(monkeypatch, parsed)

    delta = propose.propose(graph, Engine(graph), "finished the sandbox work")
    assert [c.node for c in delta.changes] == ["tools.sandbox"]
    assert delta.changes[0].confidence == 0.95
    assert delta.unmatched == ["something about dashboards"]
    assert delta_mod.validate(delta, graph) == []


def test_proposal_noops_are_dropped(workspace, monkeypatch):
    graph = load_graph(workspace)
    parsed = propose._Proposal(
        changes=[
            propose._Change(
                node="agent.ingest",
                field="status",
                value="done",
                why="",
                confidence=1.0,
            )
        ]
    )
    _stub(monkeypatch, parsed)
    assert not propose.propose(graph, Engine(graph), "ingest is done")


def test_identical_prose_is_not_reasked(workspace, monkeypatch):
    graph = load_graph(workspace)
    parsed = propose._Proposal(
        changes=[
            propose._Change(
                node="tools.sandbox",
                field="status",
                value="done",
                why="",
                confidence=1.0,
            )
        ]
    )
    client = _stub(monkeypatch, parsed)
    cache = Cache()

    first = propose.propose(graph, Engine(graph), "sandbox is done", cache)
    second = propose.propose(graph, Engine(graph), "sandbox is done", cache)
    assert client.calls == 1
    assert first.as_dict() == second.as_dict()


def test_a_changed_graph_invalidates_a_cached_proposal(workspace, monkeypatch):
    graph = load_graph(workspace)
    parsed = propose._Proposal(
        changes=[
            propose._Change(
                node="tools.streaming",
                field="status",
                value="in_progress",
                why="",
                confidence=1.0,
            )
        ]
    )
    client = _stub(monkeypatch, parsed)
    cache = Cache()

    propose.propose(graph, Engine(graph), "starting streaming", cache)
    moved = graph.with_overlay({"tools.sandbox": {"status": "done"}})
    propose.propose(moved, Engine(moved), "starting streaming", cache)
    assert client.calls == 2


def test_inventory_carries_gates_and_contracts(workspace):
    graph = load_graph(workspace)
    engine = Engine(graph)
    inventory = propose.build_inventory(graph, engine, graph.ids())
    assert "gate start: agent.ingest.done and contract.trace_format.live" in inventory
    assert "implemented by: tools.registry, tools.sandbox" in inventory
    assert "[contract]" in inventory


def test_context_selection_keeps_contracts_and_named_nodes():
    nodes = {
        f"w.n{i}": {"id": f"w.n{i}", "title": f"Widget {i}", "status": "not_started"}
        for i in range(80)
    }
    nodes["w"] = {"id": "w", "title": "Widgets", "status": "in_progress"}
    nodes["contract.c"] = {
        "id": "contract.c",
        "kind": "contract",
        "status": "agreed",
        "title": "A contract",
    }
    graph = Graph({k: node_from_dict(v) for k, v in nodes.items()})

    chosen = propose.select_context(graph, "finished w.n7 today", limit=10)
    assert "contract.c" in chosen
    assert "w.n7" in chosen
    assert len(chosen) <= 12


def test_small_graphs_send_everything(workspace):
    graph = load_graph(workspace)
    assert propose.select_context(graph, "anything") == graph.ids()


# -- end to end through the CLI ---------------------------------------------


def test_cli_set_applies_and_journals(workspace, capsys):
    code = cli.main(
        ["--graph", str(workspace), "set", "tools.sandbox", "status=done", "--yes"]
    )
    assert code == 0
    assert load_graph(workspace).get("tools.sandbox").status == "done"
    assert journal.read(workspace)[0]["writes"][0]["after"] == "done"
    assert "unlocks:" in capsys.readouterr().out


def test_cli_dry_run_writes_nothing(workspace, capsys):
    original = (workspace / "tools.yaml").read_text()
    code = cli.main(
        ["--graph", str(workspace), "set", "tools.sandbox", "status=done", "--dry-run"]
    )
    assert code == 0
    assert (workspace / "tools.yaml").read_text() == original
    assert "dry run" in capsys.readouterr().out


def test_cli_refuses_without_confirmation(workspace):
    original = (workspace / "tools.yaml").read_text()
    code = cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done"])
    assert code == 1  # stdin is not a tty under pytest, so this declines
    assert (workspace / "tools.yaml").read_text() == original


def test_cli_rejects_an_invalid_status(workspace, capsys):
    code = cli.main(
        ["--graph", str(workspace), "set", "tools.sandbox", "status=shipped", "--yes"]
    )
    assert code == 2
    assert load_graph(workspace).get("tools.sandbox").status == "in_progress"


def test_cli_multi_node_set(workspace):
    code = cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "tools.sandbox",
            "status=done",
            "agent.plan@status=done",
            "--yes",
        ]
    )
    assert code == 0
    reloaded = load_graph(workspace)
    assert reloaded.get("tools.sandbox").status == "done"
    assert reloaded.get("agent.plan").status == "done"


def test_cli_log_runs_the_full_loop(workspace, monkeypatch, capsys):
    """cmd_log is thin, but it is the only caller wiring propose to the writer."""
    parsed = propose._Proposal(
        changes=[
            propose._Change(
                node="tools.sandbox",
                field="status",
                value="done",
                why="finished the sandbox",
                confidence=0.9,
            )
        ],
        unmatched=["something about dashboards"],
    )
    _stub(monkeypatch, parsed)

    code = cli.main(
        ["--graph", str(workspace), "log", "finished the sandbox work", "--yes"]
    )
    assert code == 0
    assert load_graph(workspace).get("tools.sandbox").status == "done"

    entry = journal.read(workspace)[0]
    assert entry["origin"] == "log"
    assert entry["text"] == "finished the sandbox work"
    assert entry["unmatched"] == ["something about dashboards"]

    out = capsys.readouterr().out
    assert "confidence 90%" in out
    assert "unmatched: something about dashboards" in out
    assert "contracts that go live:" in out


def test_cli_log_reports_a_model_failure_without_writing(workspace, monkeypatch):
    original = (workspace / "tools.yaml").read_text()

    def boom():
        raise propose.ProposalError("model call failed: nope")

    monkeypatch.setattr(propose, "_client", boom)
    code = cli.main(["--graph", str(workspace), "log", "anything", "--yes"])
    assert code == 2
    assert (workspace / "tools.yaml").read_text() == original


# -- corrections -------------------------------------------------------------


from trellis.model import is_retreat


def test_retreat_distinguishes_correction_from_decision():
    assert is_retreat("done", "in_progress")  # it was not actually done
    assert is_retreat("agreed", "draft")  # the agreement came undone
    assert not is_retreat("in_progress", "done")  # progress
    assert not is_retreat("done", "abandoned")  # a decision to stop, not an error
    assert not is_retreat("done", "superseded")  # replaced, not wrong


def test_a_correction_is_recorded_with_its_reason(workspace):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "tools.sandbox",
            "status=in_progress",
            "--because",
            "the tests were never run",
            "-y",
        ]
    )
    corrections = journal.corrections(workspace)
    assert len(corrections) == 1
    assert corrections[0].node == "tools.sandbox"
    assert corrections[0].before == "done"
    assert corrections[0].reason == "the tests were never run"


def test_forward_progress_is_not_a_correction(workspace):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    assert journal.corrections(workspace) == []


def test_dropping_work_is_not_a_correction(workspace):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    cli.main(
        ["--graph", str(workspace), "set", "tools.sandbox", "status=abandoned", "-y"]
    )
    assert journal.corrections(workspace) == []


def test_correction_is_announced_before_it_is_applied(workspace, capsys):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    capsys.readouterr()
    cli.main(
        ["--graph", str(workspace), "set", "tools.sandbox", "status=in_progress", "-y"]
    )
    assert "correction: tools.sandbox goes back from done to in_progress" in (
        capsys.readouterr().out
    )


def test_dry_run_shows_the_correction_without_recording_one(workspace, capsys):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    capsys.readouterr()
    cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "tools.sandbox",
            "status=in_progress",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert "correction:" in out and "dry run" in out
    assert len(journal.corrections(workspace)) == 0


def test_repeated_corrections_are_a_doctor_finding(workspace, capsys):
    for status in ("done", "in_progress", "done", "in_progress"):
        cli.main(
            [
                "--graph",
                str(workspace),
                "set",
                "tools.sandbox",
                f"status={status}",
                "-y",
            ]
        )
    capsys.readouterr()
    cli.main(["--graph", str(workspace), "doctor"])
    out = capsys.readouterr().out
    assert "corrected 2 times" in out
    assert "no reason recorded - that lesson is gone" in out


def test_a_correction_with_a_reason_is_not_a_lost_lesson(workspace, capsys):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "tools.sandbox",
            "status=in_progress",
            "--because",
            "tests never run",
            "-y",
        ]
    )
    capsys.readouterr()
    cli.main(["--graph", str(workspace), "doctor"])
    assert "that lesson is gone" not in capsys.readouterr().out


def test_history_shows_why(workspace, capsys):
    cli.main(["--graph", str(workspace), "set", "tools.sandbox", "status=done", "-y"])
    cli.main(
        [
            "--graph",
            str(workspace),
            "set",
            "tools.sandbox",
            "status=in_progress",
            "--because",
            "tests never run",
            "-y",
        ]
    )
    capsys.readouterr()
    cli.main(["--graph", str(workspace), "history"])
    assert "why: tests never run" in capsys.readouterr().out
