"""Rendering a slice of the graph as a diagram.

Text is linear: you read what is printed, in the order it is printed. Some
questions are about *shape* — where things converge, what sits between two
subsystems — and a list cannot show convergence.

Mermaid because GitHub renders it inline, so a slice can be pasted into an
issue or a PR and read by someone who does not have trellis installed. That
matters more than fidelity here: the audience for a picture is usually the
person you are trying to agree with.

Slices, not whole graphs. Forty nodes of anything is unreadable, and a diagram
nobody can read is worse than the list it replaced.
"""

from __future__ import annotations

from .engine import Derived, Engine
from .model import Graph

# Readiness -> mermaid class. Deliberately few: a diagram that encodes six
# states in six colours is a legend, not a picture.
_CLASSES = {
    "blocked": "blocked",
    "ready": "ready",
    "active": "ready",
    "done": "settled",
    "live": "settled",
    "unverified": "ready",
    "superseded": "muted",
    "abandoned": "muted",
    "draft": "blocked",
    "unagreed": "blocked",
    "pending": "blocked",
}

_STYLES = """    classDef blocked fill:#fde2e2,stroke:#b04141,color:#5c1a1a;
    classDef ready fill:#e2f0d9,stroke:#4f7a3a,color:#1f3313;
    classDef settled fill:#e8e8e8,stroke:#7a7a7a,color:#333333;
    classDef muted fill:#f4f4f4,stroke:#bbbbbb,color:#888888;"""


def _safe(node_id: str) -> str:
    return node_id.replace(".", "_").replace("-", "_")


def _label(graph: Graph, node_id: str, derived: Derived) -> str:
    node = graph.get(node_id)
    title = node.title if node.title != node_id else node_id
    title = title.replace('"', "'")
    return f'{_safe(node_id)}["{title}<br/><small>{node_id}</small>"]'


def select(
    graph: Graph,
    around: str | None = None,
    hops: int = 1,
    contracts_only: bool = False,
    blocked_only: bool = False,
    derived: dict[str, Derived] | None = None,
) -> set[str]:
    """Which nodes belong in this slice."""
    if around:
        chosen = {around}
        frontier = {around}
        for _ in range(hops):
            nxt: set[str] = set()
            for node_id in frontier:
                nxt |= set(graph.references_of(node_id))
                nxt |= set(graph.referrers_of(node_id))
            nxt -= chosen
            chosen |= nxt
            frontier = nxt
        return chosen

    chosen = set(graph.ids())
    if contracts_only:
        # Contracts plus whoever touches them: a contract alone shows nothing.
        keep: set[str] = set()
        for node_id in graph.ids():
            if graph.get(node_id).kind == "contract":
                keep.add(node_id)
                keep |= set(graph.references_of(node_id))
                keep |= set(graph.referrers_of(node_id))
        chosen = keep
    if blocked_only and derived:
        chosen = {
            n
            for n in chosen
            if derived[n].readiness in ("blocked", "draft", "unagreed", "pending")
        }
    return chosen


def mermaid(engine: Engine, nodes: set[str]) -> str:
    """A flowchart of the given nodes and the requirement edges among them."""
    graph = engine.graph
    derived = engine.all_derived()
    lines = ["flowchart LR"]

    # Group by parent so subsystems read as subsystems.
    parents = sorted({graph.get(n).parent for n in nodes if graph.get(n).parent})
    placed: set[str] = set()
    for parent in parents:
        children = sorted(n for n in nodes if graph.get(n).parent == parent)
        if not children:
            continue
        title = graph.get(parent).title if parent in graph else parent
        lines.append(f'    subgraph {_safe(parent)}_box["{title}"]')
        for child in children:
            lines.append(f"        {_label(graph, child, derived[child])}")
            placed.add(child)
        lines.append("    end")

    for node_id in sorted(nodes - placed):
        lines.append(f"    {_label(graph, node_id, derived[node_id])}")

    # Drawn prerequisite -> dependent, which is the opposite of how the edge is
    # stored. `A requires B` renders as `B --> A` so the diagram reads left to
    # right the way the work actually flows; an arrow pointing at what something
    # needs reads as flow going backwards to everyone who is not the author.
    for node_id in sorted(nodes):
        for target in graph.references_of(node_id):
            if target in nodes:
                lines.append(f"    {_safe(target)} --> {_safe(node_id)}")

    lines.append(_STYLES)
    for node_id in sorted(nodes):
        cls = _CLASSES.get(derived[node_id].readiness)
        if cls:
            lines.append(f"    class {_safe(node_id)} {cls};")
    return "\n".join(lines)
