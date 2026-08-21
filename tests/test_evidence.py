"""Evidence: the signals that challenge a declaration rather than trust it."""

import subprocess
from datetime import UTC, datetime

import pytest

from trellis import evidence, journal, queries
from trellis.loader import load_graph
from trellis.model import Graph, ModelError, node_from_dict

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def build(*nodes: dict) -> Graph:
    return Graph({n["id"]: node_from_dict(n) for n in nodes})


def git(repo, *args, at: str | None = None):
    env = None
    if at:
        env = {"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    import os

    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A git repo whose graph has a settled node and a churning one."""
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "settled.yaml").write_text(
        "id: settled\ntitle: Settled\nstatus: in_progress\n"
    )
    (graph_dir / "churn.yaml").write_text(
        "id: churn\nkind: contract\nstatus: draft\nversion: 1\n"
    )
    for i in range(4):
        (graph_dir / f"quiet{i}.yaml").write_text(
            f"id: quiet{i}\ntitle: Quiet {i}\nstatus: not_started\n"
        )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "initial", at="2026-06-01T10:00:00Z")

    for i in range(2, 8):
        (graph_dir / "churn.yaml").write_text(
            f"id: churn\nkind: contract\nstatus: draft\nversion: {i}\n"
        )
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", f"rev {i}", at=f"2026-08-0{i}T10:00:00Z")
    return graph_dir


def test_history_counts_revisions_per_file(repo):
    history = evidence.file_history(repo)
    assert history["churn.yaml"][0] == 7  # initial plus six revisions
    assert history["settled.yaml"][0] == 1


def test_churn_is_measured_against_this_graphs_median(repo):
    ev = evidence.gather(repo, load_graph(repo), now=NOW)
    assert ev["churn"].band == "churning"
    assert ev["settled"].band == "settled"
    assert [e.node for e in evidence.churning(ev)] == ["churn"]


def test_age_is_reported_from_the_last_change(repo):
    ev = evidence.gather(repo, load_graph(repo), now=NOW)
    assert ev["settled"].age_days == 80  # untouched since 2026-06-01
    assert ev["settled"].source == "git"


def test_stale_challenges_only_declarations_claiming_motion(repo):
    """`quiet0` is just as old as `settled` but claims nothing, so it is accurate."""
    graph = load_graph(repo)
    ev = evidence.gather(repo, graph, now=NOW)
    assert ev["settled"].age_days == ev["quiet0"].age_days == 80

    stale = {e.node for e in evidence.stale(graph, {}, ev, max_age_days=14)}
    assert "settled" in stale  # in_progress and untouched for 80 days
    assert "quiet0" not in stale  # not_started for 80 days is not a lie

    # the same node, no longer claiming to move, stops being challenged
    parked = graph.with_overlay({"settled": {"status": "not_started"}})
    assert not evidence.stale(parked, {}, ev, max_age_days=14)


def test_an_undecided_contract_goes_stale_too(repo):
    """A draft nobody has touched is a decision nobody is making."""
    graph = load_graph(repo)
    ev = evidence.gather(repo, graph, now=NOW)
    assert ev["churn"].age_days == 13
    assert "churn" not in {e.node for e in evidence.stale(graph, {}, ev, 14)}
    assert "churn" in {e.node for e in evidence.stale(graph, {}, ev, 10)}


def test_a_recent_declaration_is_not_challenged(repo):
    graph = load_graph(repo)
    ev = evidence.gather(repo, graph, now=datetime(2026, 8, 8, tzinfo=UTC))
    assert [e.node for e in evidence.stale(graph, {}, ev, 14)] == ["settled"]


def test_journal_beats_git_for_per_node_precision(repo):
    graph = load_graph(repo)

    class W:
        def as_dict(self):
            return {
                "node": "churn",
                "field": "status",
                "before": "draft",
                "after": "draft",
            }

    journal.record(repo, "set", "touched it", [W()])
    ev = evidence.gather(repo, graph, now=NOW)
    assert ev["churn"].source == "journal"
    assert ev["churn"].age_days == 0


def test_shared_files_are_flagged_as_imprecise(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "many.yaml").write_text(
        "nodes:\n  - id: a\n    status: done\n  - id: b\n    status: done\n"
    )
    ev = evidence.gather(graph_dir, load_graph(graph_dir), now=NOW)
    assert ev["a"].shares_file_with == 1
    assert not ev["a"].precise


def test_no_git_degrades_to_unknown_not_to_a_wrong_answer(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "n.yaml").write_text("id: a\nstatus: in_progress\n")
    ev = evidence.gather(graph_dir, load_graph(graph_dir), now=NOW)
    assert ev["a"].last_change is None
    assert ev["a"].age_days is None
    assert ev["a"].source == "unknown"
    assert evidence.stale(load_graph(graph_dir), {}, ev, 14) == []


# -- the structural asymmetries the review identified ------------------------


def test_a_draft_contract_everyone_waits_on_is_flagged():
    """The exact failure from the review: two projects, one undrafted seam."""
    graph = build(
        {"id": "d3", "kind": "contract", "status": "draft"},
        {"id": "p1", "status": "not_started", "gates": {"start": "d3.live"}},
        {"id": "p2", "status": "not_started", "gates": {"start": "d3.live"}},
    )
    problems = queries.check(graph)
    undrafted = [p for p in problems if p.code == "undrafted_contract"]
    assert len(undrafted) == 1
    assert undrafted[0].severity == "warn"
    assert "p1, p2" in undrafted[0].message


def test_a_contract_with_consumers_but_no_implementer_is_flagged():
    graph = build(
        {"id": "c", "kind": "contract", "status": "agreed"},
        {"id": "p", "status": "not_started", "gates": {"start": "c.live"}},
    )
    codes = [p.code for p in queries.check(graph)]
    assert "unimplemented_contract" in codes


def test_an_implementer_does_not_count_as_a_consumer():
    """satisfied_by creates an edge; that must not read as demand."""
    graph = build(
        {"id": "c", "kind": "contract", "status": "agreed", "satisfied_by": ["impl"]},
        {"id": "impl", "status": "done"},
    )
    codes = [p.code for p in queries.check(graph)]
    assert "unconsumed_contract" in codes
    assert "unimplemented_contract" not in codes


def test_unparented_leaf_is_a_question_about_ownership():
    graph = build(
        {"id": "orphan", "status": "not_started"},
        {"id": "sys", "status": "in_progress"},
        {"id": "sys.child", "parent": "sys", "status": "done"},
    )
    unowned = [p for p in queries.check(graph) if p.code == "unowned_node"]
    assert [p.node for p in unowned] == ["orphan"]
    assert unowned[0].severity == "info"


def test_contracts_are_never_flagged_as_unowned():
    graph = build({"id": "c", "kind": "contract", "status": "draft"})
    assert not [p for p in queries.check(graph) if p.code == "unowned_node"]


def test_the_shipped_example_surfaces_its_undrafted_seam():
    """The example's handoff contract is `proposed` with a consumer waiting.

    That is reported, but as advice rather than a warning: proposed means it is
    on the table. A `draft` in the same position would be a warning.
    """
    from tests.test_trellis import EXAMPLE

    graph = load_graph(EXAMPLE)
    problems = queries.check(graph)
    assert not [p for p in problems if p.severity in ("error", "warn")]
    advice = [p for p in problems if p.code == "undrafted_contract"]
    assert [p.node for p in advice] == ["contract.stage_handoff"]


# -- provenance --------------------------------------------------------------


def evidenced() -> Graph:
    # a and b are unfinished on purpose, so `c`'s gate is unmet and there is
    # something for explain to walk.
    return build(
        {"id": "a", "status": "not_started"},
        {"id": "b", "status": "not_started"},
        {
            "id": "c",
            "status": "not_started",
            "gates": {"start": "a.done and b.done"},
            "evidence": {"a": {"how": "verified", "at": "2026-06-01"}, "b": "inferred"},
        },
    )


def test_evidence_parses_mapping_and_shorthand():
    node = evidenced().get("c")
    assert node.evidence_map["a"].how == "verified"
    assert node.evidence_map["a"].at == "2026-06-01"
    assert node.evidence_map["b"].how == "inferred"
    assert node.evidence_map["b"].at is None


def test_unknown_how_rejected():
    with pytest.raises(ModelError, match="expected one of"):
        node_from_dict(
            {
                "id": "x",
                "status": "done",
                "gates": {"start": "y.done"},
                "evidence": {"y": "vibes"},
            }
        )


def test_annotating_an_edge_does_not_invalidate_the_cache():
    """Provenance changes reporting, never computation, so it stays out of the
    fingerprint — otherwise nobody would ever annotate anything."""
    plain = node_from_dict({"id": "c", "status": "done", "gates": {"start": "a.done"}})
    marked = node_from_dict(
        {
            "id": "c",
            "status": "done",
            "gates": {"start": "a.done"},
            "evidence": {"a": "verified"},
        }
    )
    assert plain.fingerprint() == marked.fingerprint()


def test_edges_are_annotated_with_their_provenance():
    claims = {c.target: c for c in evidence.edges(evidenced(), now=NOW)}
    assert claims["a"].how == "verified"
    assert claims["a"].age_days == 81
    assert claims["b"].how == "inferred"
    assert claims["b"].age_days is None


def test_unconfirmed_edges_are_the_ones_to_go_check():
    claims = evidence.edges(evidenced(), now=NOW)
    assert [c.target for c in evidence.unconfirmed(claims)] == ["b"]


def test_a_verification_has_a_shelf_life():
    claims = evidence.edges(evidenced(), now=NOW)
    assert [c.target for c in evidence.stale_verifications(claims, 14)] == ["a"]
    assert evidence.stale_verifications(claims, 365) == []


def test_coverage_and_opt_in_reporting():
    claims = evidence.edges(evidenced(), now=NOW)
    assert evidence.coverage(claims) == (2, 2)
    assert evidence.uses_provenance(claims)

    bare = build(
        {"id": "a", "status": "done"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
    )
    bare_claims = evidence.edges(bare, now=NOW)
    assert evidence.coverage(bare_claims) == (0, 1)
    assert not evidence.uses_provenance(bare_claims)


def test_evidence_for_an_edge_that_no_longer_exists_is_flagged():
    graph = build(
        {"id": "a", "status": "done"},
        {"id": "b", "status": "done"},
        {
            "id": "c",
            "status": "not_started",
            "gates": {"start": "a.done"},
            "evidence": {"b": "verified"},
        },
    )
    dead = [p for p in queries.check(graph) if p.code == "dead_evidence"]
    assert len(dead) == 1 and dead[0].severity == "warn"


def test_evidence_naming_an_unknown_node_is_an_error():
    graph = build(
        {"id": "a", "status": "done"},
        {
            "id": "c",
            "status": "not_started",
            "gates": {"start": "a.done"},
            "evidence": {"ghost": "verified"},
        },
    )
    assert any(p.code == "dangling_evidence" for p in queries.check(graph))


def test_explain_carries_the_provenance_of_each_edge():
    from trellis.engine import Engine

    reasons = queries.explain(Engine(evidenced()), "c")
    by_target = {child.node: child for reason in reasons for child in reason.children}
    assert by_target["a"].how == "verified"
    assert by_target["a"].at == "2026-06-01"
    assert by_target["b"].how == "inferred"


def test_the_example_marks_its_one_inferred_edge():
    from tests.test_trellis import EXAMPLE

    graph = load_graph(EXAMPLE)
    claims = evidence.edges(graph)
    assert [(c.source, c.target) for c in evidence.unconfirmed(claims)] == [
        ("agent.emit", "contract.stage_handoff")
    ]
    assert not [p for p in queries.check(graph) if p.severity in ("error", "warn")]


# -- inert nodes and doctor --------------------------------------------------


def test_a_node_with_no_relationships_is_inert():
    """A list item wearing a node's clothes."""
    graph = build(
        {"id": "proj", "status": "in_progress"},
        {"id": "proj.a", "parent": "proj", "status": "not_started"},
        {"id": "proj.b", "parent": "proj", "status": "not_started"},
    )
    inert = [p for p in queries.check(graph) if p.code == "inert_node"]
    assert {p.node for p in inert} == {"proj.a", "proj.b"}


def test_containment_alone_does_not_make_a_node_connected():
    """A parent depends on its children, but that is not a requirement."""
    graph = build(
        {"id": "proj", "status": "in_progress"},
        {"id": "proj.a", "parent": "proj", "status": "not_started"},
    )
    assert graph.dependents_of("proj.a") == ("proj",)
    assert graph.referrers_of("proj.a") == ()
    assert any(p.code == "inert_node" for p in queries.check(graph))


def test_a_gated_node_is_not_inert():
    graph = build(
        {"id": "proj", "status": "in_progress"},
        {"id": "proj.a", "parent": "proj", "status": "done"},
        {
            "id": "proj.b",
            "parent": "proj",
            "status": "not_started",
            "gates": {"start": "proj.a.done"},
        },
    )
    inert = {p.node for p in queries.check(graph) if p.code == "inert_node"}
    assert inert == set()  # a is required by b; b requires a


def test_a_parent_is_never_inert():
    graph = build(
        {"id": "proj", "status": "in_progress"},
        {
            "id": "proj.a",
            "parent": "proj",
            "status": "done",
            "gates": {"start": "other.done"},
        },
        {"id": "other", "status": "done", "gates": {"start": "True"}},
    )
    assert "proj" not in {
        p.node for p in queries.check(graph) if p.code == "inert_node"
    }


def test_the_example_has_no_inert_nodes():
    from tests.test_trellis import EXAMPLE

    graph = load_graph(EXAMPLE)
    assert not [p for p in queries.check(graph) if p.code == "inert_node"]


def test_doctor_reports_findings_with_remedies(tmp_path, capsys):
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: d3\n    kind: contract\n    status: draft\n"
        "  - id: p1\n    status: not_started\n    gates: {start: d3.live}\n"
    )
    code = cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    assert code == 0  # findings, but none are errors
    assert "look wrong to me" in out
    assert "nobody has agreed this" in out  # the remedy, not just the finding
    assert "questions, not corrections" in out


def test_doctor_is_honest_about_a_small_clean_graph(tmp_path, capsys):
    """Silence on a fresh graph means too small to disagree with, and says so."""
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: p\n    status: in_progress\n"
        "  - id: p.a\n    parent: p\n    status: done\n"
        "  - id: p.b\n    parent: p\n    status: not_started\n"
        "    gates: {start: p.a.done}\n"
    )
    assert cli.main(["--graph", str(graph_dir), "doctor"]) == 0
    assert "too small to disagree with" in capsys.readouterr().out


def test_doctor_exits_nonzero_on_a_real_error(tmp_path):
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "id: a\nstatus: not_started\ngates: {start: ghost.done}\n"
    )
    assert cli.main(["--graph", str(graph_dir), "doctor"]) == 1


# -- cycles whose shape has a known cause ------------------------------------


def test_gating_on_your_own_parents_published_fact_is_named():
    """#11: the docs push toward published facts without saying they are external."""
    graph = build(
        {"id": "sub", "status": "in_progress", "publishes": {"thing": "sub.impl.done"}},
        {"id": "sub.impl", "parent": "sub", "status": "done"},
        {
            "id": "sub.consumer",
            "parent": "sub",
            "status": "not_started",
            "gates": {"start": "sub.thing"},
        },
    )
    problems = queries.check(graph)
    named = [p for p in problems if p.code == "cycle_known_shape"]
    assert len(named) == 1
    assert "published by its own ancestor" in named[0].message
    assert "reference the sibling directly" in named[0].message


def test_an_implementer_gating_on_its_own_contract_is_named():
    """#12: the consumer gates on a contract; the implementer only satisfies it."""
    graph = build(
        {
            "id": "contract.shape",
            "kind": "contract",
            "status": "agreed",
            "satisfied_by": ["producer"],
        },
        {
            "id": "producer",
            "status": "in_progress",
            "gates": {"start": "contract.shape.live"},
        },
    )
    named = [p for p in queries.check(graph) if p.code == "cycle_known_shape"]
    assert len(named) == 1
    assert "only satisfies it" in named[0].message


def test_self_satisfaction_does_not_also_report_nobody_requires_it():
    """#12: two findings that contradict each other sent the reporter hunting."""
    graph = build(
        {
            "id": "contract.shape",
            "kind": "contract",
            "status": "agreed",
            "satisfied_by": ["producer"],
        },
        {
            "id": "producer",
            "status": "in_progress",
            "gates": {"start": "contract.shape.live"},
        },
    )
    codes = [p.code for p in queries.check(graph)]
    assert "unconsumed_contract" not in codes


def test_a_genuinely_unconsumed_contract_still_reports():
    graph = build(
        {
            "id": "contract.shape",
            "kind": "contract",
            "status": "agreed",
            "satisfied_by": ["producer"],
        },
        {"id": "producer", "status": "done"},
    )
    assert "unconsumed_contract" in [p.code for p in queries.check(graph)]


def test_a_parent_does_not_count_as_a_contracts_consumer():
    """`referrers_of`, not `dependents_of`: containment is not demand."""
    graph = build(
        {"id": "sys", "status": "in_progress"},
        {
            "id": "sys.contract",
            "kind": "contract",
            "parent": "sys",
            "status": "agreed",
            "satisfied_by": ["impl"],
        },
        {"id": "impl", "status": "done"},
    )
    assert "unconsumed_contract" in [p.code for p in queries.check(graph)]


def test_an_unrecognised_cycle_still_reports_plainly():
    graph = build(
        {"id": "a", "status": "not_started", "gates": {"start": "b.done"}},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
    )
    cycles = [p for p in queries.check(graph) if p.code == "cycle"]
    assert len(cycles) == 1
    assert "dependency cycle" in cycles[0].message


def test_a_recognised_cycle_does_not_crash_the_derived_pass():
    """The early return is keyed off the cycles, not off a problem code.

    A new code for a recognised shape used to slip past that guard, and check
    then crashed inside the engine.
    """
    graph = build(
        {"id": "sub", "status": "in_progress", "publishes": {"thing": "sub.impl.done"}},
        {"id": "sub.impl", "parent": "sub", "status": "done"},
        {
            "id": "sub.consumer",
            "parent": "sub",
            "status": "not_started",
            "gates": {"start": "sub.thing"},
        },
    )
    problems = queries.check(graph)  # must not raise
    assert any(p.code == "cycle_known_shape" for p in problems)
