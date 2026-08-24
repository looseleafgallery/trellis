from pathlib import Path

import pytest

from trellis import expr, queries
from trellis.cache import Cache
from trellis.engine import CycleError, Engine
from trellis.loader import load_graph
from trellis.model import Graph, ModelError, node_from_dict

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"


def build(*nodes: dict) -> Graph:
    return Graph({n["id"]: node_from_dict(n) for n in nodes})


def chain() -> Graph:
    """a <- b <- c <- d, each gated on the previous being done."""
    return build(
        {"id": "a", "status": "not_started"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.done"}},
        {"id": "c", "status": "not_started", "gates": {"start": "b.done"}},
        {"id": "d", "status": "not_started", "gates": {"start": "c.done"}},
    )


# -- expressions ------------------------------------------------------------


def test_references_extracted_from_expression():
    assert expr.references("a.done and b.c.live") == {"a.done", "b.c.live"}


def test_builtin_call_name_is_not_a_reference():
    assert expr.references('has(tools.registry, "x")') == {"tools.registry"}


def test_trace_reports_every_failing_conjunct():
    values = {"a.done": False, "b.done": True, "c.done": False}
    trace = expr.evaluate("a.done and b.done and c.done", values.__getitem__)
    assert trace.value is False
    assert sorted(t.src for t in trace.unmet()) == ["a.done", "c.done"]


def test_disjunction_reported_whole():
    trace = expr.evaluate(
        "a.done or b.done", {"a.done": False, "b.done": False}.__getitem__
    )
    unmet = trace.unmet()
    assert len(unmet) == 1 and unmet[0].op == "or"


def test_unsupported_syntax_rejected():
    with pytest.raises(expr.ExprError):
        expr.evaluate("__import__('os')", lambda _: None)


# -- readiness --------------------------------------------------------------


def test_gate_controls_readiness():
    engine = Engine(chain())
    assert engine.derived("a").readiness == "ready"  # no gate
    assert engine.derived("b").readiness == "blocked"

    engine = Engine(chain().with_overlay({"a": {"status": "done"}}))
    assert engine.derived("b").readiness == "ready"
    assert engine.derived("c").readiness == "blocked"


def test_provides_only_visible_once_done():
    graph = build(
        {"id": "a", "status": "in_progress", "provides": ["streaming"]},
        {"id": "b", "status": "not_started", "gates": {"start": 'has(a, "streaming")'}},
    )
    assert Engine(graph).derived("b").readiness == "blocked"
    done = graph.with_overlay({"a": {"status": "done"}})
    assert Engine(done).derived("b").readiness == "ready"


# -- early cutoff -----------------------------------------------------------


def test_status_change_stops_two_hops_out():
    cache = Cache()
    graph = chain()
    Engine(graph, cache).all_derived()

    after = Engine(graph.with_overlay({"a": {"status": "in_progress"}}), cache)
    after.all_derived()

    assert "a" in after.recomputed  # its own declared fields changed
    assert "b" in after.recomputed  # a's `active` export flipped
    assert {"c", "d"} <= after.reused  # b's exports did not change


def test_readiness_change_does_not_propagate_at_all():
    """b goes blocked -> ready, and nothing downstream is recomputed.

    Readiness is derived state, not an export, so it is invisible to
    dependents. Only `done` crossing the boundary moves the system.
    """
    cache = Cache()
    graph = chain()
    Engine(graph, cache).all_derived()

    after = Engine(graph.with_overlay({"a": {"status": "done"}}), cache)
    derived = after.all_derived()

    assert derived["b"].readiness == "ready"
    assert {"c", "d"} <= after.reused


def test_cache_survives_across_engines():
    cache = Cache()
    graph = chain()
    Engine(graph, cache).all_derived()
    second = Engine(graph, cache)
    second.all_derived()
    assert second.reused == {"a", "b", "c", "d"}
    assert not second.recomputed


def test_cache_persists_to_disk(tmp_path):
    path = tmp_path / "cache.json"
    graph = chain()
    first = Cache.load(path)
    Engine(graph, first).all_derived()
    first.save()

    second = Cache.load(path)
    engine = Engine(graph, second)
    engine.all_derived()
    assert engine.reused == {"a", "b", "c", "d"}


# -- contracts --------------------------------------------------------------


def contract_graph(**overrides) -> Graph:
    contract = {
        "id": "contract.schema",
        "kind": "contract",
        "status": "agreed",
        "version": 2,
        "satisfied_by": ["impl"],
    }
    contract.update(overrides)
    return build(
        {"id": "impl", "status": "not_started"},
        contract,
        {
            "id": "consumer",
            "status": "not_started",
            "gates": {"start": "contract.schema.live and contract.schema.version >= 2"},
        },
    )


def test_contract_goes_live_only_when_implemented():
    graph = contract_graph()
    assert Engine(graph).derived("contract.schema").readiness == "pending"
    assert Engine(graph).derived("consumer").readiness == "blocked"

    done = graph.with_overlay({"impl": {"status": "done"}})
    assert Engine(done).derived("contract.schema").readiness == "live"
    assert Engine(done).derived("consumer").readiness == "ready"


def test_unagreed_contract_never_goes_live():
    graph = contract_graph(status="proposed").with_overlay({"impl": {"status": "done"}})
    assert Engine(graph).derived("contract.schema").readiness == "unagreed"
    assert Engine(graph).derived("consumer").readiness == "blocked"


def test_version_bump_reblocks_consumers():
    graph = contract_graph().with_overlay({"impl": {"status": "done"}})
    assert Engine(graph).derived("consumer").readiness == "ready"
    bumped = graph.with_overlay(
        {"consumer": {"gates": {"start": "contract.schema.version >= 3"}}}
    )
    assert Engine(bumped).derived("consumer").readiness == "blocked"


def test_frozen_contract_without_implementation_is_a_violation():
    graph = contract_graph(status="frozen")
    codes = [v["code"] for v in Engine(graph).derived("contract.schema").violations]
    assert "frozen_unimplemented" in codes


# -- violations -------------------------------------------------------------


def test_done_behind_an_unmet_gate_is_flagged():
    graph = chain().with_overlay({"b": {"status": "done"}})
    codes = [v["code"] for v in Engine(graph).derived("b").violations]
    assert "gate_bypassed" in codes


def test_in_progress_behind_an_unmet_gate_is_flagged():
    graph = chain().with_overlay({"b": {"status": "in_progress"}})
    violations = Engine(graph).derived("b").violations
    assert [v["code"] for v in violations] == ["working_ahead"]


def test_unmet_finish_gate_blocks_completion():
    graph = build(
        {"id": "dep", "status": "not_started", "provides": ["x"]},
        {
            "id": "n",
            "status": "done",
            "gates": {"finish": 'has(dep, "x")'},
        },
    )
    codes = [v["code"] for v in Engine(graph).derived("n").violations]
    assert "gate_bypassed" in codes


def test_parent_done_before_children_is_flagged():
    graph = build(
        {"id": "p", "status": "done"},
        {"id": "p.one", "parent": "p", "status": "done"},
        {"id": "p.two", "parent": "p", "status": "not_started"},
    )
    codes = [v["code"] for v in Engine(graph).derived("p").violations]
    assert "parent_ahead_of_children" in codes


def test_parent_progress_rolls_up_from_leaves():
    graph = build(
        {"id": "p", "status": "in_progress"},
        {"id": "p.one", "parent": "p", "status": "done"},
        {"id": "p.two", "parent": "p", "status": "not_started"},
        {"id": "p.two.a", "parent": "p.two", "status": "done"},
        {"id": "p.two.b", "parent": "p.two", "status": "not_started"},
    )
    exports = Engine(graph).derived("p").exports
    assert exports["leaf_total"] == 3
    assert exports["leaf_done"] == 2
    assert exports["progress"] == pytest.approx(2 / 3, abs=1e-4)


def test_depending_on_abandoned_work_is_flagged():
    graph = chain().with_overlay({"a": {"status": "abandoned"}})
    codes = [v["code"] for v in Engine(graph).derived("b").violations]
    assert "depends_on_abandoned" in codes


# -- structure --------------------------------------------------------------


def test_cycles_are_detected_not_hung_on():
    graph = build(
        {"id": "x", "status": "not_started", "gates": {"start": "y.done"}},
        {"id": "y", "status": "not_started", "gates": {"start": "x.done"}},
    )
    assert queries.find_cycles(graph)
    with pytest.raises(CycleError):
        Engine(graph).derived("x")
    assert any(p.code == "cycle" for p in queries.check(graph))


def test_dangling_reference_reported():
    graph = build({"id": "a", "status": "not_started", "gates": {"start": "nope.done"}})
    assert any(p.code == "dangling_reference" for p in queries.check(graph))


def test_self_reference_reported():
    graph = build({"id": "a", "status": "not_started", "gates": {"start": "a.done"}})
    assert any(p.code == "self_reference" for p in queries.check(graph))


def test_unknown_export_reported_as_gate_error():
    graph = build(
        {"id": "a", "status": "done"},
        {"id": "b", "status": "not_started", "gates": {"start": "a.nonsense"}},
    )
    assert any(p.code == "gate_error" for p in queries.check(graph))


def test_reserved_export_name_rejected(tmp_path):
    (tmp_path / "n.yaml").write_text("id: stage.done\nstatus: not_started\n")
    with pytest.raises(ModelError, match="reserved export"):
        load_graph(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    (tmp_path / "one.yaml").write_text("id: a\nstatus: not_started\n")
    (tmp_path / "two.yaml").write_text("id: a\nstatus: done\n")
    with pytest.raises(ModelError, match="duplicate"):
        load_graph(tmp_path)


def test_flow_style_node_rejected_at_load(tmp_path):
    """It would load and no write could ever land on it, so it is refused here
    - naming the line, because that is what has to be rewritten."""
    (tmp_path / "n.yaml").write_text(
        "nodes:\n"
        "  - {id: a, title: A, status: done}\n"
        "  - {id: z, title: Z, status: not_started, gates: {start: a.done}}\n"
    )
    with pytest.raises(
        ModelError, match=r"n\.yaml:2: node 'a' is written in YAML flow style"
    ):
        load_graph(tmp_path)


def test_a_whole_file_in_flow_style_is_rejected(tmp_path):
    (tmp_path / "n.yaml").write_text("{id: a, status: done}\n")
    with pytest.raises(ModelError, match="flow style"):
        load_graph(tmp_path)


def test_a_flow_value_inside_a_block_node_still_loads(tmp_path):
    """Only the node's own mapping is judged. `gates: {start: b.done}` sits on
    a line the writer can find, and the example graph ships one."""
    (tmp_path / "n.yaml").write_text(
        "id: a\nstatus: not_started\ngates: {start: b.done}\n"
    )
    assert load_graph(tmp_path).get("a").gates == (("start", "b.done"),)


def test_invalid_status_rejected():
    with pytest.raises(ModelError, match="status"):
        node_from_dict({"id": "a", "status": "shipped"})


def test_longest_prefix_reference_resolution():
    graph = build(
        {"id": "a.b", "status": "done"},
        {"id": "a.b.c", "status": "done"},
    )
    assert graph.resolve_ref("a.b.c.done") == ("a.b.c", ("done",))
    assert graph.resolve_ref("a.b.done") == ("a.b", ("done",))


# -- queries ----------------------------------------------------------------


def test_ready_lists_only_startable_work():
    engine = Engine(chain().with_overlay({"a": {"status": "done"}}))
    assert [d.id for d in queries.ready(engine)] == ["b"]


def test_explain_reaches_the_root_cause():
    engine = Engine(chain())
    reasons = queries.explain(engine, "d")
    roots = {r.node for reason in reasons for r in reason.root_causes()}
    assert "a" in roots


def test_impact_reports_what_unlocks():
    graph = chain()
    result = queries.impact(graph, {"a": {"status": "done"}})
    assert result.unlocked == ["b"]
    assert result.newly_blocked == []


def test_impact_blast_radius_is_small():
    graph = chain()
    result = queries.impact(graph, {"a": {"status": "done"}})
    # a and b recompute; c and d are reused across the two evaluations.
    assert result.nodes_recomputed == 2
    assert result.nodes_reused == 2


def test_impact_can_change_several_nodes_at_once():
    graph = chain()
    result = queries.impact(graph, {"a": {"status": "done"}, "b": {"status": "done"}})
    assert result.unlocked == ["c"]


def test_impact_surfaces_new_violations():
    graph = chain()
    result = queries.impact(graph, {"c": {"status": "done"}})
    assert any(p.code == "gate_bypassed" for p in result.violations_introduced)


# -- the shipped example ----------------------------------------------------


def test_example_graph_is_valid():
    graph = load_graph(EXAMPLE)
    problems = queries.check(graph, Engine(graph))
    assert [p for p in problems if p.severity == "error"] == []


def test_example_contract_gates_the_pipeline():
    graph = load_graph(EXAMPLE)
    engine = Engine(graph)
    assert engine.derived("agent.tool_exec").readiness == "blocked"

    result = queries.impact(
        graph, {"tools.sandbox": {"status": "done"}, "agent.plan": {"status": "done"}}
    )
    assert "contract.tool_schema" in result.contracts_lit
    assert "agent.tool_exec" in result.unlocked
    # agent.emit stays blocked: its handoff contract is still only proposed.
    assert "agent.emit" not in result.unlocked


def test_explain_expands_a_shared_node_only_once():
    """Two branches reaching the same node must not duplicate its subtree."""
    graph = build(
        {"id": "root", "status": "not_started"},
        {"id": "left", "status": "not_started", "gates": {"start": "root.done"}},
        {"id": "right", "status": "not_started", "gates": {"start": "root.done"}},
        {
            "id": "top",
            "status": "not_started",
            "gates": {"start": "left.done and right.done"},
        },
    )
    reasons = queries.explain(Engine(graph), "top")
    expanded = [
        child for reason in reasons for child in reason.children if not child.repeat
    ]
    assert sorted(c.node for c in expanded) == ["left", "right"]
    roots = {r.node for reason in reasons for r in reason.root_causes()}
    assert roots == {"root"}


def test_explain_names_an_unagreed_contract_as_the_root_cause():
    graph = contract_graph(status="proposed").with_overlay({"impl": {"status": "done"}})
    reasons = queries.explain(Engine(graph), "consumer")
    roots = {r.node for reason in reasons for r in reason.root_causes()}
    assert roots == {"contract.schema"}


def test_explain_reaches_the_implementer_behind_a_pending_contract():
    engine = Engine(contract_graph())
    roots = {
        r.node
        for reason in queries.explain(engine, "consumer")
        for r in reason.root_causes()
    }
    assert roots == {"impl"}


# -- published facts (encapsulation) ----------------------------------------


def subsystem(gate="tools.ready") -> Graph:
    """An outsider gating on a subsystem's published fact, not its internals."""
    return build(
        {
            "id": "tools",
            "status": "in_progress",
            "publishes": {"ready": "tools.inner.done", "tier": 2},
        },
        {"id": "tools.inner", "parent": "tools", "status": "not_started"},
        {"id": "consumer", "status": "not_started", "gates": {"start": gate}},
    )


def test_published_fact_lands_in_exports():
    exports = Engine(subsystem()).derived("tools").exports
    assert exports["ready"] is False
    assert exports["tier"] == 2


def test_consumer_gates_on_the_published_fact():
    graph = subsystem()
    assert Engine(graph).derived("consumer").readiness == "blocked"
    done = graph.with_overlay({"tools.inner": {"status": "done"}})
    assert Engine(done).derived("consumer").readiness == "ready"


def test_publishing_lets_internals_be_renamed():
    """The whole point: reorganize inside, leave the consumer untouched."""
    before = Engine(subsystem().with_overlay({"tools.inner": {"status": "done"}}))
    assert before.derived("consumer").readiness == "ready"

    renamed = build(
        {
            "id": "tools",
            "status": "in_progress",
            # only this subsystem's own definition changes
            "publishes": {
                "ready": "tools.stage_one.done and tools.stage_two.done",
                "tier": 2,
            },
        },
        {"id": "tools.stage_one", "parent": "tools", "status": "done"},
        {"id": "tools.stage_two", "parent": "tools", "status": "done"},
        # the consumer's gate is byte-for-byte identical
        {"id": "consumer", "status": "not_started", "gates": {"start": "tools.ready"}},
    )
    assert Engine(renamed).derived("consumer").readiness == "ready"


def test_published_references_become_dependencies():
    graph = subsystem()
    assert "tools.inner" in graph.dependencies_of("tools")
    assert "tools" in graph.dependencies_of("consumer")
    assert "tools.inner" not in graph.dependencies_of("consumer")


def test_publishing_a_reserved_name_rejected():
    with pytest.raises(ModelError, match="shadow the built-in"):
        node_from_dict({"id": "a", "status": "done", "publishes": {"done": "True"}})


def test_publish_expression_must_be_a_string_or_literal():
    with pytest.raises(ModelError, match="published fact"):
        node_from_dict({"id": "a", "status": "done", "publishes": {"x": ["a"]}})


def test_broken_published_fact_is_a_violation_not_a_crash():
    graph = build(
        {"id": "a", "status": "done", "publishes": {"ready": "ghost.done"}},
        {"id": "b", "status": "not_started", "gates": {"start": "a.ready"}},
    )
    codes = [v["code"] for v in Engine(graph).derived("a").violations]
    assert "publish_error" in codes
    # the consumer degrades to a clear gate error rather than a wrong answer
    assert [v["code"] for v in Engine(graph).derived("b").violations] == ["gate_error"]
    assert Engine(graph).derived("b").readiness == "blocked"


def test_published_fact_cannot_reference_its_own_node():
    graph = build(
        {"id": "a", "status": "done", "publishes": {"x": "True", "y": "a.x"}},
    )
    assert any(p.code == "self_reference" for p in queries.check(graph))


def test_cycle_through_published_facts_detected():
    graph = build(
        {"id": "a", "status": "done", "publishes": {"x": "b.y"}},
        {"id": "b", "status": "done", "publishes": {"y": "a.x"}},
    )
    assert queries.find_cycles(graph)


def test_publishes_changes_the_fingerprint():
    plain = node_from_dict({"id": "a", "status": "done"})
    with_fact = node_from_dict(
        {"id": "a", "status": "done", "publishes": {"x": "True"}}
    )
    assert plain.fingerprint() != with_fact.fingerprint()


# -- reaching inside --------------------------------------------------------


def nested() -> Graph:
    return build(
        {"id": "sys", "status": "in_progress"},
        {"id": "sys.a", "parent": "sys", "status": "done"},
        {"id": "sys.a.deep", "parent": "sys.a", "status": "done"},
        {"id": "sys.b", "parent": "sys", "status": "not_started"},
        {"id": "outside", "status": "not_started"},
    )


def test_reaching_into_another_subsystem_detected():
    graph = nested()
    assert queries.reaches_inside(graph, "outside", "sys.a") == "sys"


def test_siblings_are_not_reaching_inside():
    graph = nested()
    assert queries.reaches_inside(graph, "sys.b", "sys.a") is None


def test_parent_reaching_into_its_own_children_is_fine():
    graph = nested()
    assert queries.reaches_inside(graph, "sys", "sys.a") is None


def test_nearest_breached_subsystem_reported():
    """A sibling reaching into a nested subsystem breaches the inner one."""
    graph = nested()
    assert queries.reaches_inside(graph, "sys.b", "sys.a.deep") == "sys.a"


def test_root_level_references_are_never_reaching_inside():
    graph = nested()
    assert queries.reaches_inside(graph, "sys.b", "outside") is None


def test_check_reports_reaching_inside_as_advice_not_an_error():
    graph = build(
        {"id": "sys", "status": "in_progress"},
        {"id": "sys.a", "parent": "sys", "status": "done"},
        {"id": "outside", "status": "not_started", "gates": {"start": "sys.a.done"}},
    )
    problems = queries.check(graph)
    breaches = [p for p in problems if p.code == "reaches_inside"]
    assert len(breaches) == 1
    assert breaches[0].severity == "info"
    assert not [p for p in problems if p.severity == "error"]


def test_example_uses_a_published_fact_across_subsystems():
    graph = load_graph(EXAMPLE)
    assert "streaming_results" in graph.get("tools").publishes_map
    assert "tools" in graph.dependencies_of("agent.tool_exec")
    assert "tools.streaming" not in graph.dependencies_of("agent.tool_exec")
    assert not [p for p in queries.check(graph) if p.code == "reaches_inside"]


# -- completion that has not been checked ------------------------------------


def test_unverified_work_is_complete_but_not_done():
    """`.done` stays conservative; `.complete` can proceed at risk."""
    graph = build(
        {"id": "a", "status": "done_unverified", "provides": ["thing"]},
        {"id": "strict", "status": "not_started", "gates": {"start": "a.done"}},
        {"id": "risky", "status": "not_started", "gates": {"start": "a.complete"}},
    )
    engine = Engine(graph)
    assert engine.derived("a").readiness == "unverified"
    assert engine.derived("a").exports["done"] is False
    assert engine.derived("a").exports["complete"] is True
    assert engine.derived("strict").readiness == "blocked"
    assert engine.derived("risky").readiness == "ready"


def test_done_also_reads_as_complete():
    graph = build({"id": "a", "status": "done"})
    assert Engine(graph).derived("a").exports["complete"] is True


def test_unverified_work_does_not_count_as_progress():
    graph = build(
        {"id": "p", "status": "in_progress"},
        {"id": "p.one", "parent": "p", "status": "done"},
        {"id": "p.two", "parent": "p", "status": "done_unverified"},
    )
    exports = Engine(graph).derived("p").exports
    assert exports["leaf_done"] == 1  # understates rather than overstates
    assert exports["leaf_total"] == 2
    assert exports["children_done"] is False


def test_unverified_still_has_to_have_cleared_its_gate():
    graph = chain().with_overlay({"b": {"status": "done_unverified"}})
    codes = [v["code"] for v in Engine(graph).derived("b").violations]
    assert "gate_bypassed" in codes


def test_superseded_leaves_the_denominator():
    graph = build(
        {"id": "p", "status": "in_progress"},
        {"id": "p.one", "parent": "p", "status": "done"},
        {"id": "p.two", "parent": "p", "status": "superseded"},
    )
    exports = Engine(graph).derived("p").exports
    assert exports["leaf_total"] == 1
    assert exports["progress"] == 1.0
    assert Engine(graph).derived("p.two").readiness == "superseded"


def test_superseded_is_distinct_from_abandoned():
    graph = build({"id": "a", "status": "superseded"})
    exports = Engine(graph).derived("a").exports
    assert exports["superseded"] is True
    assert exports["abandoned"] is False
    assert exports["dead"] is True


def test_depending_on_superseded_work_is_flagged():
    graph = chain().with_overlay({"a": {"status": "superseded"}})
    codes = [v["code"] for v in Engine(graph).derived("b").violations]
    assert "depends_on_abandoned" in codes


def test_reserved_names_cover_the_new_exports():
    for name in ("complete", "superseded", "dead"):
        with pytest.raises(ModelError):
            node_from_dict({"id": "n", "status": "done", "publishes": {name: "True"}})
