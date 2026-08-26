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
from .style import PLAIN, Style

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


_HTML = """<!doctype html>
<meta charset="utf-8">
<title>trellis - {title}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem; color: #222; }}
  header {{ color: #666; font-size: 13px; margin-bottom: 1.5rem; }}
</style>
<header>{title} &middot; rendered {when} &middot; this is a picture of a moment,
not a live view</header>
<pre class="mermaid">
{body}
</pre>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true }});
</script>
"""


def html(engine: Engine, nodes: set[str], title: str, when: str) -> str:
    """A self-contained page that draws the slice.

    The graph never leaves the machine — it is written into the file. Only
    mermaid itself is fetched, and only when you open the page. Worth knowing
    before opening it somewhere without a network, and worth knowing that it
    does not phone home with your project structure.
    """
    return _HTML.format(title=title, when=when, body=mermaid(engine, nodes))


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


# Readiness -> a one-character mark. Same vocabulary the tree in `state` uses,
# because two different symbol sets for the same idea is worse than none.
MARKS = {
    "blocked": "x",
    "awaiting": "?",
    "ready": ">",
    "active": "~",
    "unverified": "*",
    "done": "+",
    "superseded": "-",
    "abandoned": "-",
    "live": "+",
    "pending": "~",
    "unagreed": ".",
    "draft": ".",
}


def _slice_roots(graph: Graph, nodes: set[str]) -> list[str]:
    """Nodes in the slice that nothing else in the slice requires.

    The ends of the flow: start there and walk down to what they wait on, which
    is the direction a person reads when asking why something has not moved.
    """
    required = {t for n in nodes for t in graph.references_of(n) if t in nodes}
    roots = sorted(nodes - required)
    # A slice that is entirely one cycle has no root; start somewhere rather
    # than drawing nothing.
    return roots or sorted(nodes)[:1]


# Each level costs three columns, so a long chain runs off the screen well
# before it runs out of nodes. Past this the branch is cut and said to be cut —
# the alternative is a line nobody can read, which is worse than an admission.
MAX_DEPTH = 12


# Readiness words are at most ten characters, so a constant column keeps the
# status band in the same place from one slice to the next. Aligning to the
# widest word *present* made the column jump every time the slice changed,
# which is the opposite of what a fixed column is for.
STATUS_WIDTH = 12

# Nothing below these is waiting on anything: they are settled.
_SETTLED = {"done", "live", "superseded", "abandoned"}


def slice_leaves(engine: Engine, nodes: set[str]) -> list[str]:
    """Unsettled nodes in the slice that depend on nothing else in it.

    The bottom of the tree, which is what the whole slice is ultimately
    waiting on — the answer to the question that made someone draw it.
    """
    graph = engine.graph
    derived = engine.all_derived()
    leaves = []
    for node_id in sorted(nodes):
        if any(t in nodes for t in graph.references_of(node_id)):
            continue
        if derived[node_id].readiness in _SETTLED:
            continue
        leaves.append(node_id)
    return leaves


def summarise(engine: Engine, nodes: set[str], st: Style | None = None) -> str:
    """One line of counts, and what the slice waits on. Presentation only."""
    st = st or PLAIN
    derived = engine.all_derived()
    counts: dict[str, int] = {}
    for node_id in nodes:
        word = derived[node_id].readiness
        counts[word] = counts.get(word, 0) + 1

    ready = counts.get("ready", 0)
    lead = f"{ready} ready" if ready else st.decision("nothing in this slice is ready")
    rest = ", ".join(
        f"{n} {word}" for word, n in sorted(counts.items()) if word != "ready"
    )
    head = f"{lead} \u00b7 {rest}" if rest else lead

    leaves = slice_leaves(engine, nodes)
    if not leaves:
        return head
    return head + "\nthe slice waits on " + ", ".join(leaves)


def tree(
    engine: Engine,
    nodes: set[str],
    max_depth: int = MAX_DEPTH,
    st: Style | None = None,
) -> str:
    """The slice as a dependency tree, drawn for a terminal.

    A tree projection of a graph, which means a node needed by two others
    appears twice — the second marked rather than redrawn, the same way
    `explain` handles a shared branch. That is a deliberate trade: a general
    DAG layout in fixed-width characters becomes unreadable at exactly the size
    where you need it, and an honest repeat costs one line.

    Scaffolding is drawn faint and the marks bold, so the marks form a vertical
    band you can read in one pass without the branches competing for attention.
    Widths are measured on the unpainted text — escape sequences have length
    and would silently break every column if they were counted.
    """
    st = st or PLAIN
    graph = engine.graph
    derived = engine.all_derived()
    # Laid out in two passes: the branch column varies in width with depth, so
    # the status column can only be aligned once every row exists. Each row
    # keeps its parts separate so painting happens after measuring.
    rows: list[tuple[str, str, str, str, str]] = []
    expanded: set[str] = set()

    def remaining(node_id: str, seen: set[str]) -> int:
        """How many distinct nodes hang below this one, for an honest cut."""
        if node_id in seen:
            return 0
        seen.add(node_id)
        below = [t for t in graph.references_of(node_id) if t in nodes]
        return len(set(below) - seen) + sum(remaining(t, seen) for t in below)

    def draw(node_id: str, prefix: str, connector: str, last: bool, depth: int) -> None:
        d = derived[node_id]
        node = graph.get(node_id)
        seen = node_id in expanded
        mark = MARKS.get(d.readiness, "?")
        title = node.title if node.title != node_id else ""
        rows.append(
            (
                prefix + connector,
                mark,
                node_id,
                d.readiness + ("  (above)" if seen else ""),
                title,
            )
        )
        if seen:
            return
        expanded.add(node_id)

        children = sorted(t for t in graph.references_of(node_id) if t in nodes)
        if not children:
            return
        pad = "   " if last else st.branch_pipe
        child_prefix = prefix + pad if connector else prefix

        if depth >= max_depth:
            left_over = remaining(node_id, set(expanded) - {node_id})
            rows.append(
                (
                    child_prefix + st.branch_last,
                    "",
                    "...",
                    f"{left_over} more below",
                    "use --around to focus on part of this",
                )
            )
            return

        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            draw(
                child,
                child_prefix,
                st.branch_last if is_last else st.branch_mid,
                is_last,
                depth + 1,
            )

    roots = _slice_roots(graph, nodes)
    for index, root in enumerate(roots):
        draw(root, "", "", True, 0)
        if index != len(roots) - 1:
            rows.append(("", "", "", "", ""))

    # Measured unpainted, then painted — the reverse silently breaks columns.
    branch = max(
        (
            len(scaffold) + len(mark) + (1 if mark else 0) + len(node_id)
            for scaffold, mark, node_id, _, _ in rows
        ),
        default=0,
    )
    status = max(
        STATUS_WIDTH, max((len(state) for _, _, _, state, _ in rows), default=0)
    )

    lines = []
    for scaffold, mark, node_id, state, title in rows:
        if not node_id:
            lines.append("")
            continue
        plain = f"{scaffold}{mark}{' ' if mark else ''}{node_id}"
        painted = (
            st.scaffold(scaffold)
            + (st.mark(mark, state.split()[0]) + " " if mark else "")
            + node_id
        )
        line = painted + " " * (branch - len(plain)) + "  "
        word, _, note = state.partition("  ")
        painted_state = st.readiness(word) + (st.dim("  " + note) if note else "")
        line += painted_state + " " * (status - len(state))
        lines.append((line + "  " + title).rstrip() if title else line.rstrip())
    return "\n".join(lines)


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
