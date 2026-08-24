"""The graph's shape, so a negative result can expire exactly.

A node's fingerprint answers "did this node change". This answers "could a
relationship have appeared anywhere since I last looked" — which is what makes
*"I searched and found nothing"* a fact worth keeping rather than a note that
rots quietly.
"""

from trellis.loader import load_graph

GRAPH = """nodes:
  - id: a
    title: A
    status: in_progress
    notes: some prose
  - id: b
    title: B
    kind: contract
    status: draft
    satisfied_by: [a]
  - id: c
    title: C
    status: not_started
    gates: {start: a.done}
"""


def graph_of(tmp_path, text=GRAPH):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "g.yaml").write_text(text)
    return load_graph(graph_dir)


def test_it_is_stable_for_the_same_shape(tmp_path):
    one = graph_of(tmp_path / "one")
    two = graph_of(tmp_path / "two")
    assert one.structure_hash() == two.structure_hash()


def test_what_cannot_create_an_edge_does_not_expire_a_search(tmp_path):
    """A status change moves every derived value and no relationship.

    This is why the derived-state hash is the wrong instrument: it would expire
    a search on every status change, for reasons that cannot have affected it.
    """
    graph = graph_of(tmp_path)
    base = graph.structure_hash()

    for change in (
        {"status": "done"},
        {"title": "renamed"},
        {"notes": "reworded entirely"},
    ):
        moved = graph.with_overlay({"a": change})
        assert moved.structure_hash() == base, f"{change} should not expire a search"


def test_what_can_create_an_edge_expires_a_search(tmp_path):
    graph = graph_of(tmp_path)
    base = graph.structure_hash()

    # a gate is how a relationship is written
    assert (
        graph.with_overlay({"a": {"gates": {"start": "b.agreed"}}}).structure_hash()
        != base
    )
    # satisfied_by is the other direction of the same thing
    assert graph.with_overlay({"b": {"satisfied_by": []}}).structure_hash() != base


def test_a_new_node_expires_a_search(tmp_path):
    """The counterparty you were looking for may simply not have existed yet."""
    before = graph_of(tmp_path / "before").structure_hash()
    after = graph_of(
        tmp_path / "after",
        GRAPH + "  - id: d\n    title: D\n    status: not_started\n",
    ).structure_hash()
    assert after != before


def test_it_reads_only_declared_fields(tmp_path):
    """Kernel-level: a pure function of the declaration, with no clock in it.

    Called twice on the same graph it returns the same answer, which is the
    property that lets a recorded search be trusted at all.
    """
    graph = graph_of(tmp_path)
    assert graph.structure_hash() == graph.structure_hash()
    assert len(graph.structure_hash()) == 16
