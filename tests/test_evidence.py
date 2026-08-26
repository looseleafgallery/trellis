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
    assert "2 warn" in out  # the tally, which replaced "N thing(s) look wrong"
    assert "nobody has agreed this" in out  # the remedy, not just the finding
    assert "questions, not corrections" in out


def test_doctor_is_honest_about_a_small_clean_graph(tmp_path, capsys):
    """Silence on a fresh graph is mostly silence about what could not run.

    It used to guess - "either a good graph or a graph too small to disagree
    with, probably the second" - which is a plausible cause offered as a
    finding. The tool knows the real answer: with no git and no journal, the
    age, volatility, correction and drift checks never ran (#41).
    """
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
    out = capsys.readouterr().out
    assert "nothing looks wrong across 3 nodes" in out
    assert "not checked here" in out
    assert "no node has a commit or a journal entry" in out
    assert "this graph has no journal" in out
    assert "no corroborators are configured" in out
    assert "only as wide as what it compared" in out
    # The structural half did run, and saying so is the other half of scope.
    assert "structure: gates, references, contracts, cycles" in out


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


# -- ranking and acknowledgement ---------------------------------------------


def noisy_graph() -> Graph:
    return build(
        {"id": "spike", "status": "in_progress"},
        {"id": "sys", "status": "in_progress"},
        {"id": "sys.a", "parent": "sys", "status": "done"},
        {
            "id": "sys.b",
            "parent": "sys",
            "status": "not_started",
            "gates": {"start": "sys.a.done and ghost.done"},
        },
    )


def test_findings_are_ranked_by_what_to_fix_first():
    """Severity says how bad; urgency says what to do first."""
    problems = queries.check(noisy_graph())
    assert problems[0].code == "dangling_reference"  # blocks evaluation entirely
    codes = [p.code for p in problems]
    assert codes.index("dangling_reference") < codes.index("inert_node")
    # ranking is total and stable
    assert problems == sorted(problems, key=lambda p: p.rank)


def test_severity_still_outranks_urgency():
    problems = queries.check(noisy_graph())
    severities = [queries.SEVERITY_ORDER[p.severity] for p in problems]
    assert severities == sorted(severities)


def test_an_unknown_code_ranks_in_the_middle_rather_than_first_or_last():
    p = queries.Problem("brand_new_code", "warn", "n", "")
    assert p.rank[1] == queries.DEFAULT_URGENCY


def test_acknowledging_a_true_but_permanent_finding_silences_it():
    """Two spike-only projects correctly have no relationships. Saying so every
    run is how a whole severity gets tuned out."""
    graph = build(
        {"id": "spike", "status": "in_progress", "acknowledge": ["inert_node"]},
    )
    codes = [p.code for p in queries.check(graph)]
    assert "inert_node" not in codes
    assert "unowned_node" in codes  # only what was acknowledged is silenced


def test_acknowledged_findings_are_counted_not_hidden():
    graph = build(
        {"id": "spike", "status": "in_progress", "acknowledge": ["inert_node"]},
    )
    _kept, muted = queries.check_with_muted(graph)
    assert muted == 1


def test_an_acknowledgement_that_no_longer_fires_is_reported():
    """Same idea as dead evidence: nothing else would ever tell you."""
    graph = build(
        {"id": "a", "status": "done", "acknowledge": ["inert_node"]},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
    )
    dead = [p for p in queries.check(graph) if p.code == "dead_acknowledgement"]
    assert len(dead) == 1
    assert "no longer fires" in dead[0].message


def test_acknowledge_accepts_a_bare_string():
    node = node_from_dict({"id": "a", "status": "done", "acknowledge": "inert_node"})
    assert node.acknowledges("inert_node")


def test_acknowledge_accepts_a_reason_beside_the_code():
    """The only place a writer with no terminal can put one."""
    node = node_from_dict(
        {
            "id": "a",
            "status": "in_progress",
            "acknowledge": [{"code": "inert_node", "why": "spike only, one ticket"}],
        }
    )
    assert node.acknowledges("inert_node")
    assert node.acknowledged_why("inert_node") == "spike only, one ticket"


def test_a_reasoned_acknowledgement_still_silences_the_finding():
    """The two forms differ in what they record, never in what they do."""
    graph = build(
        {
            "id": "spike",
            "status": "in_progress",
            "acknowledge": [{"code": "inert_node", "why": "nothing gates on it"}],
        },
    )
    codes = [p.code for p in queries.check(graph)]
    assert "inert_node" not in codes
    _kept, muted = queries.check_with_muted(graph)
    assert muted == 1


def test_the_two_acknowledge_forms_mix_in_one_list():
    """Additive: adding a reason to one entry cannot disturb the others."""
    node = node_from_dict(
        {
            "id": "a",
            "status": "in_progress",
            "acknowledge": ["unowned_node", {"code": "inert_node", "why": "a spike"}],
        }
    )
    assert node.acknowledges("unowned_node") and node.acknowledges("inert_node")
    assert node.acknowledged_why("inert_node") == "a spike"
    # Absent is not blank-with-a-reason: the file simply does not say.
    assert node.acknowledged_why("unowned_node") == ""


def test_an_acknowledge_entry_needs_a_code():
    """A reason attached to nothing silences nothing, and would do it quietly."""
    with pytest.raises(ModelError, match="needs a `code`"):
        node_from_dict(
            {"id": "a", "status": "done", "acknowledge": [{"why": "because"}]}
        )


def test_an_acknowledge_entry_refuses_a_key_it_does_not_know():
    """`reason:` parses as an entry with no reason, which is the silent failure."""
    with pytest.raises(ModelError, match="unknown key"):
        node_from_dict(
            {
                "id": "a",
                "status": "done",
                "acknowledge": [{"code": "inert_node", "reason": "mistyped"}],
            }
        )


def test_acknowledge_does_not_change_the_fingerprint():
    """It changes what is reported, never what is computed."""
    plain = node_from_dict({"id": "a", "status": "done"})
    acked = node_from_dict({"id": "a", "status": "done", "acknowledge": ["inert_node"]})
    assert plain.fingerprint() == acked.fingerprint()


def test_a_reason_does_not_change_the_fingerprint_either():
    """Prose about a finding cannot reach a derived value; nobody would write
    one if annotating dropped every cache entry."""
    bare = node_from_dict({"id": "a", "status": "done", "acknowledge": ["inert_node"]})
    reasoned = node_from_dict(
        {
            "id": "a",
            "status": "done",
            "acknowledge": [{"code": "inert_node", "why": "a spike"}],
        }
    )
    assert bare.fingerprint() == reasoned.fingerprint()


def test_an_overlay_carries_the_reasons_through():
    """The preview is the thing that gets applied. A round-trip that dropped
    them would render `no reason recorded` for a node that records one."""
    graph = build(
        {
            "id": "a",
            "status": "not_started",
            "acknowledge": [{"code": "inert_node", "why": "a spike"}],
        },
    )
    after = graph.with_overlay({"a": {"status": "done"}})
    assert after.get("a").acknowledged_why("inert_node") == "a spike"


# -- slices: what is this holding up, and what does it look like -------------


def pipeline() -> Graph:
    return build(
        {"id": "a", "status": "in_progress"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
        {"id": "c", "status": "not_started", "gates": {"start": "b.done"}},
        {"id": "d", "status": "not_started", "gates": {"start": "a.done and z.done"}},
        {"id": "z", "status": "not_started"},
    )


def test_blocking_separates_unlocks_now_from_waiting_downstream():
    """The two numbers people conflate: 'frees one' vs 'is on the path of three'."""
    from trellis.engine import Engine

    result = queries.blocking(Engine(pipeline()), "a")
    # b starts the moment a lands; d also needs z, so it does not
    assert result.unlocks == ["b"]
    assert result.waiting == ["b", "c", "d"]


def test_unlocks_agrees_with_what_impact_would_say():
    """Computed through the same what-if path, so the numbers cannot diverge."""
    from trellis.cache import Cache
    from trellis.engine import Engine

    graph = pipeline()
    result = queries.blocking(Engine(graph), "a")
    predicted = queries.impact(graph, {"a": {"status": "done"}}, Cache())
    assert result.unlocks == predicted.unlocked


def test_a_contract_lands_by_being_agreed_not_done():
    from trellis.engine import Engine

    graph = build(
        {"id": "c", "kind": "contract", "status": "draft", "satisfied_by": ["impl"]},
        {"id": "impl", "status": "done"},
        {"id": "user", "status": "not_started", "gates": {"start": "c.live"}},
    )
    assert queries.blocking(Engine(graph), "c").unlocks == ["user"]


def test_containment_is_not_counted_as_waiting():
    """A parent depends on its children for rollup but is not waiting on them."""
    from trellis.engine import Engine

    graph = build(
        {"id": "p", "status": "in_progress"},
        {"id": "p.child", "parent": "p", "status": "not_started"},
    )
    assert queries.blocking(Engine(graph), "p.child").waiting == []


def test_chokepoints_rank_by_how_much_is_waiting():
    from trellis.engine import Engine

    points = queries.chokepoints(Engine(pipeline()))
    assert points[0].node == "a"
    assert len(points[0].waiting) == 3


def test_finished_work_is_not_a_chokepoint():
    from trellis.engine import Engine

    graph = pipeline().with_overlay({"a": {"status": "done"}})
    assert "a" not in [b.node for b in queries.chokepoints(Engine(graph))]


# -- edge sensitivity: which edge, if wrong, costs the most ------------------


def test_reads_through_counts_the_source_and_everything_behind_it():
    from trellis.engine import Engine

    # b requires a, c requires b: the edge b -> a is read by b and by c.
    result = queries.edge_sensitivity(Engine(pipeline()), "b", "a")
    assert result.reads_through == ["b", "c"]


def test_a_leaf_edge_reads_one_not_none():
    """`1` is the true count - the source's own derivation reads the edge.

    Reporting `0` would be the tool saying nothing depends on it, which is a
    stronger claim than the one it computed.
    """
    from trellis.engine import Engine

    assert queries.edge_sensitivity(Engine(pipeline()), "c", "b").reads_through == ["c"]


def test_removing_the_only_requirement_frees_the_source():
    from trellis.engine import Engine

    result = queries.edge_sensitivity(Engine(pipeline()), "b", "a")
    assert result.differs == ["b"]
    assert result.also_dropped == []


def test_one_of_two_unmet_requirements_changes_nothing():
    """d needs a *and* z, so lifting either leaves it blocked on the other.

    This is why the counterfactual cannot carry the ordering on its own: it is
    zero for most edges, and zero is not "nothing depends on this".
    """
    from trellis.engine import Engine

    result = queries.edge_sensitivity(Engine(pipeline()), "d", "a")
    assert result.differs == []
    assert result.reads_through == ["d"]


def test_the_other_requirement_survives_the_removal():
    """Removing one edge must not quietly remove the conjunct beside it."""
    graph = pipeline()
    after = graph.with_overlay(queries.without_edge(graph, "d", "a"))
    assert after.get("d").gate("start") == "z.done"
    assert after.references_of("d") == ("z",)


def test_a_contract_edge_is_removed_from_satisfied_by():
    from trellis.engine import Engine

    graph = build(
        {"id": "c", "kind": "contract", "status": "agreed", "satisfied_by": ["impl"]},
        {"id": "impl", "status": "done"},
        {"id": "user", "status": "not_started", "gates": {"start": "c.live"}},
    )
    assert queries.edge_sensitivity(Engine(graph), "c", "impl").differs == ["c", "user"]


def test_a_shared_term_is_reported_rather_than_counted_silently():
    """The count is an upper bound when one term names two nodes. Say which."""
    from trellis.engine import Engine

    graph = build(
        {"id": "a", "status": "not_started"},
        {"id": "b", "status": "not_started"},
        {
            "id": "c",
            "status": "not_started",
            "gates": {"start": "a.progress + b.progress >= 1.0"},
        },
    )
    result = queries.edge_sensitivity(Engine(graph), "c", "a")
    assert result.also_dropped == ["b"]


def test_an_edge_from_a_published_fact_says_it_was_not_measured():
    """A published fact is a value, not a requirement, so there is nothing to
    lift. An unmeasured edge must not arrive looking like a measured zero."""
    from trellis.engine import Engine

    graph = build(
        {"id": "a", "status": "not_started"},
        {"id": "p", "status": "not_started", "publishes": {"ok": "a.done"}},
    )
    result = queries.edge_sensitivity(Engine(graph), "p", "a")
    assert not result.measured
    assert "published fact ok" in result.unavailable
    assert result.differs == []


def test_an_edge_that_is_not_there_is_refused_not_measured():
    """A measured zero and an edge nobody ever wrote look identical otherwise,
    and reporting the second as the first is the mistake this repo keeps
    finding: a fact about where we looked, dressed as a fact about the graph."""
    from trellis.engine import Engine

    with pytest.raises(KeyError, match="no such edge"):
        queries.edge_sensitivity(Engine(pipeline()), "b", "z")


def test_edges_are_ranked_by_what_reads_through_them():
    from trellis.engine import Engine

    graph = pipeline()
    pairs = [("d", "z"), ("b", "a"), ("c", "b")]
    ranked = [(e.source, e.target) for e in queries.rank_edges(Engine(graph), pairs)]
    # b -> a is read by b and c; the other two are read by one node each, and
    # ties fall to the ids so the walk is the same every time.
    assert ranked == [("b", "a"), ("c", "b"), ("d", "z")]


def test_an_unmeasurable_edge_ranks_last():
    from trellis.engine import Engine

    graph = build(
        {"id": "a", "status": "not_started"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
        {"id": "p", "status": "not_started", "publishes": {"ok": "a.done"}},
    )
    ranked = queries.rank_edges(Engine(graph), [("p", "a"), ("b", "a")])
    assert [(e.source, e.target) for e in ranked] == [("b", "a"), ("p", "a")]


def test_the_overlay_is_never_something_the_writer_could_apply():
    """Research may not loosen execution: the counterfactual rewrites a
    `gates:` block, and that is exactly what the write path refuses to do."""
    from trellis.delta import EDITABLE_FIELDS

    graph = pipeline()
    overlay = queries.without_edge(graph, "b", "a")
    assert "gates" in overlay["b"]
    assert "gates" not in EDITABLE_FIELDS


def test_mermaid_draws_prerequisite_to_dependent():
    """`b requires a` renders `a --> b`, so the diagram reads as the work flows."""
    from trellis import viz
    from trellis.engine import Engine

    engine = Engine(pipeline())
    out = viz.mermaid(engine, {"a", "b"})
    assert "a --> b" in out
    assert "b --> a" not in out


def test_a_slice_only_draws_edges_inside_itself():
    from trellis import viz
    from trellis.engine import Engine

    out = viz.mermaid(Engine(pipeline()), {"a", "b"})
    edges = [line.strip() for line in out.splitlines() if "-->" in line]
    assert edges == ["a --> b"]


def test_selecting_around_a_node_walks_both_directions():
    from trellis import viz

    graph = pipeline()
    assert viz.select(graph, around="b", hops=1) == {"a", "b", "c"}
    assert "d" in viz.select(graph, around="b", hops=2)


def test_children_are_grouped_under_their_parent():
    from trellis import viz
    from trellis.engine import Engine

    graph = build(
        {"id": "sys", "title": "A subsystem", "status": "in_progress"},
        {"id": "sys.a", "parent": "sys", "status": "done"},
    )
    out = viz.mermaid(Engine(graph), {"sys", "sys.a"})
    assert 'subgraph sys_box["A subsystem"]' in out


def test_readiness_is_rendered_as_a_class():
    from trellis import viz
    from trellis.engine import Engine

    out = viz.mermaid(Engine(pipeline()), {"a", "b"})
    assert "class b blocked;" in out


# -- waiting on a person, not on work ----------------------------------------


def awaiting_graph() -> Graph:
    return build(
        {"id": "storage", "status": "not_started", "awaiting": "which backend"},
        {"id": "api", "status": "not_started", "gates": {"start": "storage.done"}},
        {"id": "pickup", "status": "not_started"},
    )


def test_a_decision_owed_is_not_ready():
    from trellis.engine import Engine

    engine = Engine(awaiting_graph())
    assert engine.derived("storage").readiness == "awaiting"
    assert engine.derived("pickup").readiness == "ready"


def test_ready_excludes_it():
    """The gate is open, but nobody can actually pick it up."""
    from trellis.engine import Engine

    picked = [d.id for d in queries.ready(Engine(awaiting_graph()))]
    assert picked == ["pickup"]


def test_blocked_by_work_outranks_a_decision():
    """If the gate is shut, the work is the truth."""
    from trellis.engine import Engine

    graph = build(
        {"id": "a", "status": "not_started"},
        {
            "id": "b",
            "status": "not_started",
            "gates": {"start": "a.done"},
            "awaiting": "a call nobody has made",
        },
    )
    assert Engine(graph).derived("b").readiness == "blocked"


def test_progress_outranks_a_decision():
    from trellis.engine import Engine

    graph = build({"id": "a", "status": "in_progress", "awaiting": "something"})
    assert Engine(graph).derived("a").readiness == "active"


def test_it_is_exported_so_a_gate_can_read_it():
    from trellis.engine import Engine

    engine = Engine(awaiting_graph())
    assert engine.derived("storage").exports["awaiting"] is True
    assert engine.derived("pickup").exports["awaiting"] is False


def test_it_changes_the_fingerprint():
    """Unlike evidence or acknowledgements, this changes what readiness is."""
    plain = node_from_dict({"id": "a", "status": "not_started"})
    owed = node_from_dict({"id": "a", "status": "not_started", "awaiting": "a call"})
    assert plain.fingerprint() != owed.fingerprint()


def test_check_reports_it_as_its_own_class():
    problems = [
        p for p in queries.check(awaiting_graph()) if p.code == "awaiting_decision"
    ]
    assert len(problems) == 1
    assert "which backend" in problems[0].message


def test_explain_downstream_names_the_kind_of_push_needed():
    from trellis.engine import Engine

    reasons = queries.explain(Engine(awaiting_graph()), "api")
    detail = reasons[0].children[0].detail
    assert detail == "awaiting a decision, not blocked by work"


def test_an_undecided_node_goes_stale(repo):
    """A decision nobody has touched in weeks is a decision nobody is making."""
    graph = load_graph(repo).with_overlay(
        {"quiet0": {"status": "not_started", "awaiting": "a long-unmade call"}}
    )
    ev = evidence.gather(repo, graph, now=NOW)
    assert "quiet0" in {e.node for e in evidence.stale(graph, {}, ev, 14)}


def test_a_contract_is_never_flagged_as_awaiting_a_decision():
    """Contracts already say this with `unagreed`; two words for it would be worse."""
    graph = build({"id": "c", "kind": "contract", "status": "draft"})
    assert not [p for p in queries.check(graph) if p.code == "awaiting_decision"]


# -- ids a gate cannot reach -------------------------------------------------


def test_a_hyphenated_id_is_reported_as_unreachable():
    """`a-b` parses as subtraction, so the reference silently becomes two names."""
    from trellis.model import is_referenceable

    assert is_referenceable("svc.a_thing")
    assert not is_referenceable("svc.a-thing")
    assert not is_referenceable("svc.a thing")

    graph = build({"id": "svc.a-thing", "status": "done"})
    problems = [p for p in queries.check(graph) if p.code == "unreferenceable_id"]
    assert len(problems) == 1
    assert "underscores" in problems[0].message


def test_a_reference_that_split_on_a_hyphen_suggests_the_real_node():
    graph = build(
        {"id": "svc.a-thing", "status": "done"},
        {
            "id": "svc.b",
            "status": "not_started",
            "gates": {"start": "svc.a-thing.done"},
        },
    )
    dangling = [p for p in queries.check(graph) if p.code == "dangling_reference"]
    assert any("did you mean 'svc.a-thing'" in p.message for p in dangling)


def test_ordinary_ids_are_not_flagged():
    graph = build({"id": "agent.tool_exec", "status": "done"})
    assert not [p for p in queries.check(graph) if p.code == "unreferenceable_id"]


# -- absence where you looked is not absence ---------------------------------


def test_history_is_the_same_however_the_path_is_spelled(repo, monkeypatch):
    """`_git` runs `git -C <dir>`, so a relative pathspec resolved twice.

    `--graph graph` from a project root asked git for `graph/graph`, matched
    nothing, and every node was reported as having no history.
    """
    absolute = evidence.file_history(repo)
    assert absolute, "fixture has no history"

    monkeypatch.chdir(repo.parent)
    assert evidence.file_history("graph") == absolute
    monkeypatch.chdir(repo)
    assert evidence.file_history(".") == absolute


def test_volatility_survives_a_relative_path(repo, monkeypatch):
    monkeypatch.chdir(repo.parent)
    ev = evidence.gather("graph", load_graph("graph"), now=NOW)
    assert ev["churn"].band == "churning"
    assert ev["settled"].age_days == 80


def test_being_in_git_is_asked_of_git(repo):
    assert evidence.in_git(repo)


def test_a_directory_outside_any_repo_is_known_to_be_outside(tmp_path):
    """Its own tmp_path: the repo fixture git-inits the one it is given, so a
    subdirectory of that is inside the repository."""
    bare = tmp_path / "graph"
    bare.mkdir()
    assert not evidence.in_git(bare)


# -- a clean summary says what it compared -----------------------------------
#
# `docs/BOUNDARY.md` requires this of a corroborator. The kernel's own summary
# lines were making the same claim by silence (#41).


@pytest.fixture
def checkable(tmp_path):
    """A structurally clean graph in git, annotated, with enough files to band.

    Everything the trust layer reads is present here, which is the case the
    scope line has to be able to distinguish from its opposite.
    """
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: p\n    title: Parent\n    status: in_progress\n"
        "  - id: p.a\n    title: A\n    parent: p\n    status: done\n"
        "  - id: p.b\n    title: B\n    parent: p\n    status: not_started\n"
        "    gates: {start: p.a.done}\n"
        "    evidence:\n"
        "      p.a: {how: verified, at: '2026-08-20'}\n"
        "  - id: p.c\n    title: C\n    parent: p\n    status: not_started\n"
        "    gates: {start: p.b.done}\n"
        "    evidence:\n"
        "      p.b: {how: verified, at: '2026-08-20'}\n"
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "initial", at="2026-08-20T10:00:00Z")
    return graph_dir


def test_doctor_says_what_it_could_check_when_it_can_check_it(checkable, capsys):
    """The same clean line on a graph in git, with a journal, reads differently.

    The point is not that the message is longer. It is that the two runs are
    distinguishable at all - before this, a graph nothing could be checked
    against and a graph everything checked out on printed one sentence.
    """
    from trellis import cli

    cli.main(["--graph", str(checkable), "set", "p.b", "status=in_progress", "-y"])
    capsys.readouterr()

    assert cli.main(["--graph", str(checkable), "doctor", "--stale-after", "3650"]) == 0
    out = capsys.readouterr().out
    assert "nothing looks wrong across 4 nodes" in out
    assert "age and staleness: all 4 declaration(s), dated from" in out
    assert "volatility: 4 declaration(s), against this graph's own median" in out
    assert "corrections and drift: from this graph's journal" in out
    assert "edge provenance: 2 of 2 edge(s) carry `evidence:`" in out
    assert "no node has a commit or a journal entry" not in out
    assert "this graph has no journal" not in out


def test_trust_does_not_call_an_unrunnable_check_a_clean_one(repo, capsys):
    """`nothing to challenge` covered six checks and named two.

    This graph is in git and has no journal, and its files are too few for a
    volatility median to mean anything. Both are reasons a check did not run,
    and neither is a reason to believe the declaration.
    """
    from trellis import cli

    small = repo.parent / "small"
    small.mkdir()
    (small / "g.yaml").write_text("id: only\ntitle: Only\nstatus: not_started\n")
    git(repo.parent, "add", "-A")
    git(repo.parent, "commit", "-q", "-m", "one node", at="2026-08-20T10:00:00Z")

    assert cli.main(["--graph", str(small), "trust"]) == 0
    out = capsys.readouterr().out
    assert "nothing to challenge in what could be checked here" in out
    assert "not checked here" in out
    assert "1 node(s) have file history; 4 are needed for a median" in out
    assert "corrections: this graph has no journal" in out
    # The old line asserted this as a finding. It is true, so it stays - as
    # something that was checked, under a heading that says so.
    assert "age and staleness: all 1 declaration(s)" in out


def test_check_does_not_claim_more_than_the_declaration(tmp_path, capsys):
    """`ok - N nodes, no problems` reads as a clean bill of health.

    `check` never opens git or the journal, so a graph whose every node went
    stale a month ago passes it. The line is true and the scope is the half
    that makes it readable.
    """
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: p\n    status: in_progress\n"
        "  - id: p.a\n    parent: p\n    status: not_started\n"
        "    gates: {start: p.b.done}\n"
        "  - id: p.b\n    parent: p\n    status: done\n"
    )
    assert cli.main(["--graph", str(graph_dir), "check"]) == 0
    out = capsys.readouterr().out
    assert "no structural problems" in out
    assert "the declaration only" in out
    assert "`trellis doctor`" in out


def test_a_missing_named_gate_is_not_the_absence_of_every_gate(tmp_path, capsys):
    """`explain n --gate finish` said "nothing gates this node".

    What it had established is that `finish` is not declared. `start` was,
    which is a different sentence (#41).
    """
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: a\n    status: done\n"
        "  - id: b\n    status: not_started\n    gates: {start: a.done}\n"
    )
    assert (
        cli.main(["--graph", str(graph_dir), "explain", "b", "--gate", "finish"]) == 0
    )
    out = capsys.readouterr().out
    assert "nothing gates this node" not in out
    assert "no 'finish' gate declared - this node has 'start'" in out
    assert "is unmet" not in out  # `start` is satisfied here


def test_the_gate_that_was_not_asked_about_is_not_assumed_met(tmp_path, capsys):
    """Only the named gate is evaluated, so the others' state is unknown here.

    Reporting them as met would be this bug rediscovered inside its own fix,
    so the unmet ones are named and the way to ask about them is given.
    """
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n"
        "  - id: a\n    status: not_started\n"
        "  - id: b\n    status: not_started\n    gates: {start: a.done}\n"
    )
    assert (
        cli.main(["--graph", str(graph_dir), "explain", "b", "--gate", "finish"]) == 0
    )
    out = capsys.readouterr().out
    assert "no 'finish' gate declared - this node has 'start'" in out
    assert "'start' is unmet; ask about it with --gate start" in out


def test_a_node_with_no_gates_at_all_still_says_so(tmp_path, capsys):
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text("id: a\nstatus: not_started\n")
    assert (
        cli.main(["--graph", str(graph_dir), "explain", "a", "--gate", "finish"]) == 0
    )
    assert "no other gate either" in capsys.readouterr().out


def test_nothing_holding_anything_up_names_what_it_looked_at(tmp_path, capsys):
    """`chokepoints` considers open work only, and the clean line did not say."""
    from trellis import cli

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(
        "nodes:\n  - id: a\n    status: done\n  - id: b\n    status: done\n"
    )
    assert cli.main(["--graph", str(graph_dir), "blocking", "--all"]) == 0
    assert "no open node has anything downstream of it" in capsys.readouterr().out


# -- drawing for a person at a terminal --------------------------------------


def test_the_tree_reads_top_down_from_what_waits_to_what_it_waits_on():
    from trellis import viz
    from trellis.engine import Engine

    engine = Engine(pipeline())
    out = viz.tree(engine, {"a", "b", "c"})
    lines = [line for line in out.splitlines() if line.strip()]
    # c waits on b waits on a, so c leads and a is deepest
    assert lines[0].startswith("~ c") or lines[0].startswith("x c")
    assert "a" in lines[-1]
    assert lines[-1].startswith(" ") or "-" in lines[-1]


def test_a_node_needed_twice_is_marked_rather_than_redrawn():
    """A tree projection of a graph: the repeat is honest and costs one line."""
    from trellis import viz
    from trellis.engine import Engine

    graph = build(
        {"id": "shared", "status": "done"},
        {"id": "l", "status": "not_started", "gates": {"start": "shared.done"}},
        {"id": "r", "status": "not_started", "gates": {"start": "shared.done"}},
        {"id": "top", "status": "not_started", "gates": {"start": "l.done and r.done"}},
    )
    out = viz.tree(Engine(graph), set(graph.ids()))
    assert out.count("(above)") == 1
    assert out.count("shared") == 2


def test_the_status_column_stays_aligned_at_depth():
    from trellis import viz
    from trellis.engine import Engine

    out = viz.tree(Engine(pipeline()), {"a", "b", "c", "d", "z"})
    columns = {
        line.index(state)
        for line in out.splitlines()
        if line.strip()
        for state in ["blocked", "ready", "active", "done"]
        if state in line
    }
    assert len(columns) == 1, f"status column drifts: {columns}"


def test_a_slice_with_no_root_still_draws():
    """Every node required by another leaves no head to start from."""
    from trellis import viz
    from trellis.engine import Engine

    graph = build(
        {"id": "a", "status": "done"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
    )
    assert viz.tree(Engine(graph), {"a"}).strip()


def test_html_embeds_the_graph_rather_than_pointing_at_it():
    """Nothing about the project leaves the machine; only mermaid is fetched."""
    from trellis import viz
    from trellis.engine import Engine

    page = viz.html(Engine(pipeline()), {"a", "b"}, "slice", "2026-01-01T00:00:00Z")
    assert "flowchart LR" in page
    assert "a --> b" in page
    assert "2026-01-01T00:00:00Z" in page
    assert "not a live view" in page


def chain(length: int) -> Graph:
    nodes = [{"id": "n0", "status": "done"}]
    nodes += [
        {"id": f"n{i}", "status": "not_started", "gates": {"start": f"n{i - 1}.done"}}
        for i in range(1, length)
    ]
    return build(*nodes)


def test_a_deep_chain_is_cut_rather_than_run_off_the_screen():
    """Each level costs three columns, so depth runs out of screen long before
    it runs out of nodes."""
    from trellis import viz
    from trellis.engine import Engine

    graph = chain(60)
    out = viz.tree(Engine(graph), set(graph.ids()))
    assert max(len(line) for line in out.splitlines()) < 100
    assert "more below" in out
    assert "--around" in out


def test_the_cut_says_how_much_it_cut():
    from trellis import viz
    from trellis.engine import Engine

    graph = chain(30)
    out = viz.tree(Engine(graph), set(graph.ids()), max_depth=5)
    marker = next(line for line in out.splitlines() if "more below" in line)
    # 30 nodes, 6 drawn (root plus five levels), so 24 are not
    assert "24 more below" in marker


def test_a_shallow_graph_is_never_cut():
    from trellis import viz
    from trellis.engine import Engine

    graph = build(
        {"id": "top", "status": "not_started", "gates": {"start": "a.done and b.done"}},
        {"id": "a", "status": "done"},
        {"id": "b", "status": "done"},
    )
    assert "more below" not in viz.tree(Engine(graph), set(graph.ids()))
