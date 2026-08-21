"""Questions you ask the graph.

`check` validates the declaration. `ready` and `state` report where things
stand. `explain` walks unmet requirements down to their root causes. `impact`
answers the what-if: given one small change, what does the whole system look
like — and how much of that answer had to be recomputed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import expr as expr_mod
from . import journal
from .cache import Cache
from .engine import CycleError, Derived, Engine
from .model import Graph, is_referenceable, node_from_dict

# How urgently a finding wants attention, within its severity. Severity says
# how bad it is; this says what to do first. Fourteen findings ranked only by
# severity and then alphabetically is a list you skim rather than act on.
URGENCY = {
    # Nothing else can be trusted until these are gone: they stop the graph
    # from evaluating at all.
    "cycle": 0,
    "cycle_known_shape": 0,
    "dangling_reference": 0,
    "dangling_evidence": 0,
    "legacy_journal": 2,
    "unreferenceable_id": 0,
    "self_reference": 0,
    "gate_parse_error": 0,
    "unknown_parent": 0,
    "unknown_implementer": 0,
    # Expressions that exist but do not evaluate.
    "gate_error": 1,
    "publish_error": 1,
    # The declaration contradicts itself.
    "gate_bypassed": 2,
    "parent_ahead_of_children": 2,
    "frozen_unimplemented": 2,
    # Something real is waiting on somebody.
    "undrafted_contract": 3,
    "unimplemented_contract": 3,
    "working_ahead": 3,
    "depends_on_abandoned": 3,
    # Modelling smells: true, but nothing is stuck because of them.
    "reaches_inside": 4,
    "dead_evidence": 4,
    "unconsumed_contract": 4,
    "orphan_contract": 4,
    "rollup_lagging": 5,
    "awaiting_decision": 3,
    "inert_node": 5,
    "unowned_node": 5,
}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
DEFAULT_URGENCY = 3


@dataclass
class Problem:
    code: str
    severity: str
    node: str
    message: str

    @property
    def rank(self) -> tuple[int, int, str]:
        """Sort key: how bad, then what to fix first, then stable by node."""
        return (
            SEVERITY_ORDER.get(self.severity, 3),
            URGENCY.get(self.code, DEFAULT_URGENCY),
            self.node,
        )

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "node": self.node,
            "message": self.message,
        }


def find_cycles(graph: Graph) -> list[list[str]]:
    """Every dependency cycle, as node-id paths. Empty means the graph is a DAG."""
    cycles: list[list[str]] = []
    state: dict[str, int] = {}  # 0 = visiting, 1 = done
    path: list[str] = []

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            return
        if state.get(node_id) == 0:
            cycle = [*path[path.index(node_id) :], node_id]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        state[node_id] = 0
        path.append(node_id)
        for dep in graph.dependencies_of(node_id):
            visit(dep)
        path.pop()
        state[node_id] = 1

    for node_id in graph.ids():
        visit(node_id)
    return cycles


def ancestors_of(graph: Graph, node_id: str) -> list[str]:
    """Parent chain, nearest first."""
    out: list[str] = []
    seen = {node_id}
    current = graph.get(node_id).parent
    while current and current in graph and current not in seen:
        out.append(current)
        seen.add(current)
        current = graph.get(current).parent
    return out


def reaches_inside(graph: Graph, source_id: str, target_id: str) -> str | None:
    """The subsystem this reference breaches, if it breaches one.

    Referring to a node inside a subsystem you are not part of couples you to
    that subsystem's internals: it can no longer be reorganized without
    breaking you. Returns the nearest such enclosing subsystem, or None when
    the reference stays within a shared boundary (siblings, or a parent
    reaching into its own children).
    """
    containing = set(ancestors_of(graph, source_id)) | {source_id}
    for ancestor in ancestors_of(graph, target_id):
        if ancestor not in containing:
            return ancestor
    return None


def _published_fact_reference(graph: Graph, node_id: str, owner: str) -> str | None:
    """The published fact of `owner` that `node_id` gates on, if any."""
    node = graph.get(node_id)
    for _gate_name, source in node.gates:
        try:
            refs = expr_mod.references(source)
        except expr_mod.ExprError:
            continue
        for dotted in refs:
            try:
                target, rest = graph.resolve_ref(dotted)
            except KeyError:
                continue
            if target == owner and rest and rest[0] in graph.get(owner).publishes_map:
                return dotted
    return None


def explain_cycle(graph: Graph, cycle: list[str]) -> str | None:
    """Name the mistake behind a cycle, when the shape is one we recognise.

    A cycle is always a real modelling error, but "a -> b -> a" describes the
    topology rather than the cause. These two shapes come from following the
    documentation slightly too far, so they are worth naming precisely.
    """
    members = {n for n in cycle if n in graph}

    # A node gating on a published fact of its own ancestor. The parent already
    # depends on its children through rollup, so this closes a loop. It comes
    # from `reaches_inside` pushing toward published facts without saying that
    # they are the *external* interface.
    for node_id in sorted(members):
        for ancestor in ancestors_of(graph, node_id):
            if ancestor not in members:
                continue
            dotted = _published_fact_reference(graph, node_id, ancestor)
            if dotted:
                return (
                    f"{node_id} gates on {dotted!r}, a fact published by its own "
                    f"ancestor {ancestor!r}. A published fact is a subsystem's "
                    f"external interface; inside the subsystem, reference the "
                    f"sibling directly"
                )

    # An implementer gating on the contract it satisfies. The consumer is
    # supposed to gate on a contract; the implementer only satisfies it.
    for node_id in sorted(members):
        node = graph.get(node_id)
        if node.kind != "contract":
            continue
        for impl in node.satisfied_by:
            if impl in members and node.id in graph.references_of(impl):
                return (
                    f"{impl} satisfies {node.id} and also gates on it. A contract "
                    f"is gated on by its consumers; its implementer only satisfies "
                    f"it"
                )
    return None


def rank(problems: list[Problem]) -> list[Problem]:
    """Most urgent first, so the top line is the one to act on."""
    return sorted(problems, key=lambda p: p.rank)


def acknowledged(graph: Graph, problems: list[Problem]) -> tuple[list[Problem], int]:
    """Split findings a node has already answered for good from the rest.

    Suppressed findings are counted and reported, never silently dropped —
    an acknowledgement you cannot see is indistinguishable from a bug.
    """
    kept, muted = [], 0
    for problem in problems:
        node = graph.nodes.get(problem.node)
        # Errors are not acknowledgeable. An observation that is true and
        # permanent is worth answering for good; a graph that cannot evaluate
        # is a defect, and letting it be dismissed would cost the property the
        # whole tool rests on — that it fails loudly.
        if (
            problem.severity != "error"
            and node is not None
            and node.acknowledges(problem.code)
        ):
            muted += 1
        else:
            kept.append(problem)
    return kept, muted


def dead_acknowledgements(graph: Graph, problems: list[Problem]) -> list[Problem]:
    """Acknowledgements for findings that no longer fire.

    Same idea as `dead_evidence`: an answer to a question nobody is asking any
    more is worth removing, and nothing else would ever tell you.
    """
    firing = {(p.node, p.code) for p in problems}
    unacknowledgeable = {p.code for p in problems if p.severity == "error"}
    out = []
    for node in graph:
        for code in node.acknowledge:
            if code in unacknowledgeable:
                out.append(
                    Problem(
                        "unacknowledgeable",
                        "warn",
                        node.id,
                        f"acknowledges {code!r}, but that is reported as an error - "
                        f"errors are defects, not opinions, and cannot be silenced",
                    )
                )
            elif (node.id, code) not in firing:
                out.append(
                    Problem(
                        "dead_acknowledgement",
                        "info",
                        node.id,
                        f"acknowledges {code!r}, which no longer fires here - "
                        f"the acknowledgement can go",
                    )
                )
    return out


def check(graph: Graph, engine: Engine | None = None, graph_dir=None) -> list[Problem]:
    """Findings worth showing: ranked, with acknowledged ones removed."""
    kept, _muted = check_with_muted(graph, engine, graph_dir)
    return kept


def check_with_muted(
    graph: Graph, engine: Engine | None = None, graph_dir=None
) -> tuple[list[Problem], int]:
    """As `check`, and also how many findings were acknowledged away.

    Callers that display results want both numbers: filtering silently would
    make an acknowledgement indistinguishable from a bug.
    """
    problems = collect(graph, engine, graph_dir)
    kept, muted = acknowledged(graph, problems)
    return rank(kept), muted


def collect(
    graph: Graph, engine: Engine | None = None, graph_dir=None
) -> list[Problem]:
    """Every finding, unranked and unfiltered."""
    problems: list[Problem] = []

    for node in graph:
        if node.parent and node.parent not in graph:
            problems.append(
                Problem(
                    "unknown_parent",
                    "error",
                    node.id,
                    f"parent {node.parent!r} is not a declared node",
                )
            )
        for impl in node.satisfied_by:
            if impl not in graph:
                problems.append(
                    Problem(
                        "unknown_implementer",
                        "error",
                        node.id,
                        f"satisfied_by names unknown node {impl!r}",
                    )
                )
        expressions = [("gate", name, src) for name, src in node.gates]
        expressions += [
            ("published fact", name, src)
            for name, src in node.publishes
            if isinstance(src, str)
        ]
        for label, expr_name, source in expressions:
            try:
                refs = expr_mod.references(source)
            except expr_mod.ExprError as exc:
                problems.append(
                    Problem("gate_parse_error", "error", node.id, f"{expr_name}: {exc}")
                )
                continue
            for dotted in refs:
                try:
                    target, _rest = graph.resolve_ref(dotted)
                except KeyError:
                    # A hyphenated id splits into two unknown names, which is a
                    # baffling way to discover the real problem.
                    split = [
                        nid
                        for nid in graph.ids()
                        if not is_referenceable(nid) and nid.startswith(dotted)
                    ]
                    hint = (
                        f" - did you mean {split[0]!r}? gates cannot reference it: "
                        f"expressions are parsed as Python, so its punctuation "
                        f"reads as an operator"
                        if split
                        else ""
                    )
                    problems.append(
                        Problem(
                            "dangling_reference",
                            "error",
                            node.id,
                            f"{label} {expr_name!r} references unknown node "
                            f"{dotted!r}{hint}",
                        )
                    )
                    continue
                if target == node.id:
                    problems.append(
                        Problem(
                            "self_reference",
                            "error",
                            node.id,
                            f"{label} {expr_name!r} references its own node",
                        )
                    )
                    continue
                breached = reaches_inside(graph, node.id, target)
                if breached:
                    problems.append(
                        Problem(
                            "reaches_inside",
                            "info",
                            node.id,
                            f"{label} {expr_name!r} references {target}, reaching inside "
                            f"subsystem {breached!r}; consider publishing a fact on "
                            f"{breached} and gating on that instead",
                        )
                    )

    for node in graph:
        referenced = set(graph.references_of(node.id))
        for item in node.evidence:
            if item.target not in graph:
                # The gate this annotates very likely named a published fact,
                # which is the thing the docs tell you to prefer across
                # subsystems. Point at the node that publishes it rather than
                # just refusing the name.
                hint = ""
                try:
                    owner, rest = graph.resolve_ref(item.target)
                except KeyError:
                    owner, rest = None, ()
                if owner and rest and rest[0] in graph.get(owner).publishes_map:
                    hint = (
                        f" - that is a fact published by {owner!r}; evidence keys "
                        f"are node ids, so annotate {owner!r}"
                    )
                problems.append(
                    Problem(
                        "dangling_evidence",
                        "error",
                        node.id,
                        f"evidence names unknown node {item.target!r}{hint}",
                    )
                )
            elif item.target not in referenced:
                problems.append(
                    Problem(
                        "dead_evidence",
                        "warn",
                        node.id,
                        f"evidence for {item.target!r} but nothing here references it "
                        f"- the edge it justified is gone",
                    )
                )

    for node in graph:
        if not is_referenceable(node.id):
            problems.append(
                Problem(
                    "unreferenceable_id",
                    "warn",
                    node.id,
                    "no gate can reference this id: expressions are parsed as "
                    "Python, so anything but letters, digits, underscores and "
                    "dots reads as an operator. Use underscores instead",
                )
            )

    legacy = journal.legacy_journal_path(graph_dir) if graph_dir else None
    if legacy is not None and legacy.exists():
        problems.append(
            Problem(
                "legacy_journal",
                "warn",
                # Not about any one node; naming it keeps the output aligned.
                "(graph)",
                f"the journal is still in {journal.LEGACY_DIRNAME}/, which is "
                f"normally gitignored - so every recorded reason is local to this "
                f"machine. Move it: git mv {journal.LEGACY_DIRNAME}/"
                f"{journal.JOURNAL_NAME} {journal.HISTORY_DIRNAME}/",
            )
        )

    cycles = find_cycles(graph)
    cycle_members: set[str] = set()
    for cycle in cycles:
        cycle_members.update(cycle)
        message = "dependency cycle: " + " -> ".join(cycle)
        cause = explain_cycle(graph, cycle)
        problems.append(
            Problem(
                "cycle" if cause is None else "cycle_known_shape",
                "error",
                cycle[0],
                f"{message}\n      {cause}" if cause else message,
            )
        )

    # Asymmetry checks need the reverse edges, so they live here rather than in
    # the (strictly local, cacheable) per-node computation.
    for node in graph:
        if node.kind == "contract":
            # `referrers_of`, not `dependents_of`: a parent reading its children
            # is not demand for the contract.
            referrers = graph.referrers_of(node.id)
            consumers = [d for d in referrers if d not in node.satisfied_by]

            # An implementer that also gates on its contract always closes a
            # cycle, so that case is reported once, by the cycle, with the fix
            # named. Nothing is added here.
            #
            # `not referrers` rather than `not consumers`: saying "no node's gate
            # requires it" while the node that does require it is named in a
            # cycle sends you looking for a second problem that is not there.
            if not referrers and node.status in ("agreed", "frozen"):
                problems.append(
                    Problem(
                        "unconsumed_contract",
                        "info",
                        node.id,
                        "agreed, but no node's gate requires it",
                    )
                )
            # The dangerous asymmetry: several things are waiting on an
            # agreement that nobody has started drafting. Each side can look
            # like it is progressing while both assume the other settled it.
            if consumers and node.status in ("draft", "proposed"):
                # `draft` means nobody has even put it forward while others
                # wait on it — the asymmetry that stalls two teams silently.
                # `proposed` is on the table and being negotiated: normal.
                drafted = node.status == "proposed"
                problems.append(
                    Problem(
                        "undrafted_contract",
                        "info" if drafted else "warn",
                        node.id,
                        f"still {node.status} but {len(consumers)} node(s) gate on it "
                        f"({', '.join(sorted(consumers))}); nobody has agreed it",
                    )
                )
            if consumers and not node.satisfied_by:
                problems.append(
                    Problem(
                        "unimplemented_contract",
                        "warn",
                        node.id,
                        f"{len(consumers)} node(s) gate on it, but nothing claims to "
                        f"implement it",
                    )
                )
            continue

        if node.awaiting and node.kind == "work":
            problems.append(
                Problem(
                    "awaiting_decision",
                    "info",
                    node.id,
                    f"waiting on a decision, not on work: {node.awaiting}",
                )
            )

        # A node that requires nothing and is required by nothing is a list
        # item wearing a node's clothes. Its only relationship is containment,
        # which the graph cannot compute anything from. Many of these at once
        # is the signature of listing work rather than modelling it.
        if (
            not graph.children_of(node.id)
            and not node.gates
            and not graph.referrers_of(node.id)
        ):
            problems.append(
                Problem(
                    "inert_node",
                    "info",
                    node.id,
                    "requires nothing and nothing requires it - it carries no "
                    "relationship the graph can use",
                )
            )

        # An unparented leaf belongs to no initiative. That is not a missing
        # field to fill in — it is a question about who owns the work.
        if not node.parent and not graph.children_of(node.id):
            problems.append(
                Problem(
                    "unowned_node",
                    "info",
                    node.id,
                    "belongs to no parent and has no children - is this owned?",
                )
            )

    if cycles:
        # Derived state is undefined until the cycle is cut, so stop here rather
        # than letting the engine raise. Keyed off the cycles themselves, not off
        # a problem code — a new code for a recognised shape used to slip past
        # this guard and crash.
        return problems

    engine = engine or Engine(graph)
    for node_id, derived in engine.all_derived().items():
        for violation in derived.violations:
            problems.append(
                Problem(
                    violation["code"],
                    violation["severity"],
                    node_id,
                    violation["message"],
                )
            )
    problems += dead_acknowledgements(graph, problems)
    return problems


def ready(engine: Engine, include_active: bool = False) -> list[Derived]:
    """Work that can be picked up right now.

    Excludes anything `awaiting` a decision: the gate is open, but a person
    owes something, so nobody can actually pick it up.
    """
    wanted = {"ready", "active"} if include_active else {"ready"}
    out = [
        d
        for d in engine.all_derived().values()
        if d.kind == "work" and d.readiness in wanted
    ]
    # Leaves first: a ready parent is rarely the thing you actually pick up.
    out.sort(key=lambda d: (bool(engine.graph.children_of(d.id)), d.id))
    return out


@dataclass
class Reason:
    """One unmet requirement, with the reasons underneath it."""

    node: str
    gate: str
    src: str
    detail: str
    children: list[Reason] = field(default_factory=list)
    # True when this node's subtree was already expanded on another branch.
    repeat: bool = False
    # How the edge that led here is believed, when the graph says.
    how: str | None = None
    at: str | None = None

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "gate": self.gate,
            "src": self.src,
            "detail": self.detail,
            "repeat": self.repeat,
            "how": self.how,
            "at": self.at,
            "children": [c.as_dict() for c in self.children],
        }

    def root_causes(self) -> list[Reason]:
        """Leaves of the chain: the things actually holding everything up.

        A repeat contributes nothing, and neither does a branch whose children
        are all repeats — whatever is under them was already counted where it
        was expanded. Returning the branch itself instead would name a node
        that is merely downstream of the real cause.
        """
        if self.repeat:
            return []
        if not self.children:
            return [self]
        out: list[Reason] = []
        for child in self.children:
            out.extend(child.root_causes())
        return out


def _describe(derived: Derived) -> str:
    if derived.readiness == "awaiting":
        # A different push entirely: nothing here is waiting on engineering.
        return "awaiting a decision, not blocked by work"
    return f"{derived.readiness} (status: {derived.status})"


def _explain_contract(
    engine: Engine,
    node_id: str,
    derived: Derived,
    _seen: frozenset[str],
    _expanded: set[str],
    max_depth: int,
) -> list[Reason]:
    """A contract that is not live is waiting on agreement, on work, or both."""
    if derived.exports.get("live"):
        return []
    node = engine.graph.get(node_id)
    reasons: list[Reason] = []
    if not derived.exports.get("agreed"):
        reasons.append(
            Reason(
                node_id,
                "agreement",
                node_id,
                f"not agreed yet (status: {node.status}) - this is a decision, not work",
            )
        )
    if not node.satisfied_by:
        reasons.append(
            Reason(node_id, "implementation", node_id, "nothing claims to implement it")
        )
    for impl in node.satisfied_by:
        if impl not in engine.graph or impl in _seen:
            continue
        if engine.derived(impl).exports.get("done"):
            continue
        reasons.append(
            _child_reason(
                engine, impl, impl, _seen | {node_id}, _expanded, max_depth, node_id
            )
        )
    return reasons


def _child_reason(
    engine: Engine,
    target: str,
    src: str,
    _seen: frozenset[str],
    _expanded: set[str],
    max_depth: int,
    source_node: str | None = None,
) -> Reason:
    """One step down the chain, expanding each node's subtree at most once.

    A node reachable by several paths is a normal shape here — two stages can
    both be waiting on the same contract. Expanding it under every path would
    make the output exponential in the graph size for no extra information.
    """
    derived = engine.derived(target)
    item = None
    if source_node and source_node in engine.graph:
        item = engine.graph.get(source_node).evidence_map.get(target)
    how = item.how if item else None
    at = item.at if item else None

    if target in _expanded:
        return Reason(
            target,
            "",
            src,
            _describe(derived) + " [detailed above]",
            repeat=True,
            how=how,
            at=at,
        )
    _expanded.add(target)
    return Reason(
        target,
        "",
        src,
        _describe(derived),
        explain(engine, target, "start", _seen | {target}, max_depth - 1, _expanded),
        how=how,
        at=at,
    )


def explain(
    engine: Engine,
    node_id: str,
    gate: str = "start",
    _seen: frozenset[str] = frozenset(),
    max_depth: int = 8,
    _expanded: set[str] | None = None,
) -> list[Reason]:
    """Why is this node not moving? Recurses through references to root causes."""
    if _expanded is None:
        _expanded = {node_id}
    derived = engine.derived(node_id)
    if max_depth <= 0:
        return []
    if derived.kind == "contract":
        return _explain_contract(engine, node_id, derived, _seen, _expanded, max_depth)

    g = derived.gates.get(gate)
    if g is None or g.satisfied:
        return []
    if g.error:
        return [
            Reason(node_id, gate, g.expr, f"gate could not be evaluated: {g.error}")
        ]

    reasons: list[Reason] = []
    for unmet in g.unmet:
        src = unmet["src"]
        reason = Reason(node_id, gate, src, f"unmet: {src}")
        for dotted in sorted(expr_mod.references(src)):
            try:
                target, _rest = engine.graph.resolve_ref(dotted)
            except KeyError:
                continue
            if target in _seen or target == node_id:
                continue
            reason.children.append(
                _child_reason(
                    engine,
                    target,
                    dotted,
                    _seen | {node_id},
                    _expanded,
                    max_depth,
                    node_id,
                )
            )
        reasons.append(reason)
    return reasons


@dataclass
class Change:
    node: str
    field_: str
    before: object
    after: object

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "field": self.field_,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class Impact:
    overlay: dict
    changes: list[Change]
    unlocked: list[str]
    newly_blocked: list[str]
    violations_introduced: list[Problem]
    violations_cleared: list[Problem]
    contracts_lit: list[str]
    contracts_dark: list[str]
    created: list[str]
    nodes_total: int
    nodes_recomputed: int
    nodes_reused: int

    def as_dict(self) -> dict:
        return {
            "overlay": self.overlay,
            "changes": [c.as_dict() for c in self.changes],
            "unlocked": self.unlocked,
            "newly_blocked": self.newly_blocked,
            "violations_introduced": [p.as_dict() for p in self.violations_introduced],
            "violations_cleared": [p.as_dict() for p in self.violations_cleared],
            "contracts_lit": self.contracts_lit,
            "contracts_dark": self.contracts_dark,
            "created": self.created,
            "cost": {
                "nodes_total": self.nodes_total,
                "recomputed": self.nodes_recomputed,
                "reused": self.nodes_reused,
            },
        }


def _violation_set(derived: dict[str, Derived]) -> dict[tuple, Problem]:
    out: dict[tuple, Problem] = {}
    for node_id, d in derived.items():
        for v in d.violations:
            out[(node_id, v["code"], v["message"])] = Problem(
                v["code"], v["severity"], node_id, v["message"]
            )
    return out


def project(
    graph: Graph, overlay: dict[str, dict], new_nodes: list[dict] | None = None
) -> Graph:
    """The graph a change would produce, without touching disk."""
    if new_nodes:
        nodes = dict(graph.nodes)
        for spec in new_nodes:
            nodes[spec["id"]] = node_from_dict(spec)
        graph = Graph(nodes)
    return graph.with_overlay(overlay)


def impact(
    graph: Graph,
    overlay: dict[str, dict],
    cache: Cache | None = None,
    new_nodes: list[dict] | None = None,
) -> Impact:
    """Apply a hypothetical change and diff the whole system against it.

    Both evaluations share one cache. Nodes the change cannot reach have
    identical keys in both runs, so the second pass reuses them outright — the
    reported `recomputed` count is the real blast radius of the change.
    """
    cache = cache if cache is not None else Cache()
    before_engine = Engine(graph, cache)
    before = before_engine.all_derived()

    after_graph = project(graph, overlay, new_nodes)
    after_engine = Engine(after_graph, cache)
    after = after_engine.all_derived()

    changes: list[Change] = []
    unlocked: list[str] = []
    newly_blocked: list[str] = []
    contracts_lit: list[str] = []
    contracts_dark: list[str] = []
    created: list[str] = []

    for node_id in sorted(set(before) | set(after)):
        b, a = before.get(node_id), after.get(node_id)
        if b is None and a is not None:
            created.append(node_id)
            changes.append(Change(node_id, "created", None, a.readiness))
            continue
        if a is None:
            changes.append(Change(node_id, "removed", b.readiness, None))
            continue
        if b.readiness != a.readiness:
            changes.append(Change(node_id, "readiness", b.readiness, a.readiness))
            if b.readiness == "blocked" and a.readiness in ("ready", "active"):
                unlocked.append(node_id)
            if b.readiness in ("ready", "active") and a.readiness == "blocked":
                newly_blocked.append(node_id)
            if a.kind == "contract":
                if a.readiness == "live" and b.readiness != "live":
                    contracts_lit.append(node_id)
                if b.readiness == "live" and a.readiness != "live":
                    contracts_dark.append(node_id)
        if b.status != a.status:
            changes.append(Change(node_id, "status", b.status, a.status))
        for gate_name in sorted(set(b.gates) | set(a.gates)):
            bg, ag = b.gates.get(gate_name), a.gates.get(gate_name)
            b_ok = bg.satisfied if bg else None
            a_ok = ag.satisfied if ag else None
            if b_ok != a_ok:
                changes.append(Change(node_id, f"gate:{gate_name}", b_ok, a_ok))
        if b.exports.get("progress") != a.exports.get("progress"):
            changes.append(
                Change(
                    node_id,
                    "progress",
                    b.exports.get("progress"),
                    a.exports.get("progress"),
                )
            )

    before_violations = _violation_set(before)
    after_violations = _violation_set(after)
    introduced = [v for k, v in after_violations.items() if k not in before_violations]
    cleared = [v for k, v in before_violations.items() if k not in after_violations]

    return Impact(
        overlay=overlay,
        changes=changes,
        unlocked=sorted(unlocked),
        newly_blocked=sorted(newly_blocked),
        violations_introduced=introduced,
        violations_cleared=cleared,
        contracts_lit=sorted(contracts_lit),
        contracts_dark=sorted(contracts_dark),
        created=sorted(created),
        nodes_total=len(after_graph),
        nodes_recomputed=len(after_engine.recomputed),
        nodes_reused=len(after_engine.reused),
    )


@dataclass
class Blocking:
    """Who is waiting on one node, split into the two questions people conflate.

    `unlocks` is what starts moving the moment this lands. `waiting` is
    everything downstream that cannot start while this is open — a larger set,
    because most of it is also waiting on other things.

    Quoting one number when you meant the other is a real and repeated mistake:
    it is the difference between "finishing this frees five people" and
    "finishing this is on the path of eleven".
    """

    node: str
    unlocks: list[str]
    waiting: list[str]

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "unlocks": self.unlocks,
            "waiting": self.waiting,
        }


def _downstream(graph: Graph, node_id: str) -> set[str]:
    """Everything that reaches `node_id` through requirement edges.

    Containment is excluded deliberately: a parent depends on its children for
    rollup, but a parent is not *waiting* on them in the sense that matters
    here, and including it would double-count every subtree.
    """
    out: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for candidate in graph.referrers_of(current):
            if candidate not in out:
                out.add(candidate)
                stack.append(candidate)
    return out


def blocking(engine: Engine, node_id: str) -> Blocking:
    """What this node is holding up, by both measures."""
    derived = engine.all_derived()
    downstream = _downstream(engine.graph, node_id)

    waiting = sorted(
        n
        for n in downstream
        if derived[n].kind == "work" and derived[n].readiness == "blocked"
    )

    # `unlocks` is computed by actually asking: readiness is derived, so the
    # honest answer is what the engine says once the node is done. Reusing the
    # what-if path means this number cannot drift from what `impact` reports.
    node = engine.graph.get(node_id)
    # A contract "landing" means being agreed; work landing means being done.
    settled = "agreed" if node.kind == "contract" else "done"
    overlay = {node_id: {"status": settled}}
    after = Engine(engine.graph.with_overlay(overlay), engine.cache).all_derived()
    unlocks = sorted(
        n
        for n in after
        if derived[n].readiness == "blocked"
        and after[n].readiness in ("ready", "active")
    )
    return Blocking(node=node_id, unlocks=unlocks, waiting=waiting)


def chokepoints(engine: Engine, limit: int = 10) -> list[Blocking]:
    """Nodes ranked by how much is waiting on them.

    Only open work is considered — a finished node is not a chokepoint. Costs
    one what-if evaluation per candidate, which the shared cache makes cheap
    because most of each graph is unaffected by any single change.
    """
    derived = engine.all_derived()
    candidates = [
        n
        for n, d in derived.items()
        if d.readiness not in ("done", "abandoned", "superseded", "live")
    ]
    scored = [blocking(engine, n) for n in candidates]
    scored = [b for b in scored if b.unlocks or b.waiting]
    scored.sort(key=lambda b: (-len(b.waiting), -len(b.unlocks), b.node))
    return scored[:limit]


def summary(engine: Engine) -> dict:
    """Counts by readiness, plus roots with their rollup progress."""
    derived = engine.all_derived()
    counts: dict[str, int] = {}
    for d in derived.values():
        counts[d.readiness] = counts.get(d.readiness, 0) + 1
    roots = [n.id for n in engine.graph if n.kind == "work" and not n.parent]
    return {
        "counts": counts,
        "roots": {
            r: {
                "progress": derived[r].exports.get("progress", 0.0),
                "leaf_done": derived[r].exports.get("leaf_done", 0),
                "leaf_total": derived[r].exports.get("leaf_total", 0),
                "readiness": derived[r].readiness,
            }
            for r in sorted(roots)
        },
    }


__all__ = [
    "Change",
    "CycleError",
    "Impact",
    "Problem",
    "Reason",
    "acknowledged",
    "ancestors_of",
    "check",
    "explain",
    "find_cycles",
    "impact",
    "project",
    "reaches_inside",
    "ready",
    "summary",
]
