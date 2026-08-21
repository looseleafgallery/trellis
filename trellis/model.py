"""The declared graph: what a human writes down.

Everything here is *declared* state — the assertions you make about your work.
Nothing in this module is computed; derived state lives in engine.py.

That split is load-bearing. Declared state is the only mutable input to the
system, so it is the only thing whose change can invalidate a cached
computation. Keeping it in its own layer means a node's cache key can be a
hash of its declared fields plus its dependencies' exports, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

WORK_STATUSES = (
    "not_started",
    "in_progress",
    # Complete and green, but nobody has checked it. Calling this `done` is the
    # collapse that costs you, so it exports `done: false` and `complete: true`
    # — gates saying `.done` stay conservative, gates saying `.complete` can
    # proceed at risk, and the choice is visible in the gate.
    "done_unverified",
    "done",
    # Replaced by something else, as opposed to dropped. The distinction
    # changes whether you delete the node or keep it as a pointer.
    "superseded",
    "abandoned",
)
CONTRACT_STATUSES = ("draft", "proposed", "agreed", "frozen")
KINDS = ("work", "contract")

# Export names the engine computes. A node id whose last segment collides with
# one of these would make `a.b.done` ambiguous, so the loader rejects it.
RESERVED_EXPORTS = frozenset(
    {
        "done",
        "active",
        "live",
        "provides",
        "version",
        "children_done",
        "progress",
        "leaf_done",
        "leaf_total",
        "frozen",
        "status",
        "complete",
        "superseded",
        "dead",
    }
)


# How an edge came to be believed, most trustworthy first. Closed so it can be
# reported consistently — an open vocabulary here would defeat the purpose.
HOW = ("verified", "stated", "inferred", "assumed")


# How far along each status is, within its own track. Used to tell a
# correction (a belief being revised) from a decision or normal progress.
# `abandoned` and `superseded` are terminal but sideways: leaving them out
# means dropping work never reads as an error.
STATUS_RANK = {
    "not_started": 0,
    "in_progress": 1,
    "done_unverified": 2,
    "done": 3,
    "draft": 0,
    "proposed": 1,
    "agreed": 2,
    "frozen": 3,
}


def is_retreat(before: object, after: object) -> bool:
    """Whether a status change walks backwards along its own track.

    `done -> in_progress` is someone saying it was not actually done: a
    correction. `done -> abandoned` is a decision to stop, which is not the
    same thing and is deliberately not counted.
    """
    if before not in STATUS_RANK or after not in STATUS_RANK:
        return False
    return STATUS_RANK[after] < STATUS_RANK[before]


class ModelError(ValueError):
    """A declared node is malformed."""


@dataclass(frozen=True)
class EdgeEvidence:
    """Why we believe an edge out of this node holds.

    An edge checked against a system of record and one read out of a prose
    sentence render identically without this, so a reader trusts them uniformly
    and should not.
    """

    target: str
    how: str
    # ISO date the claim was checked. A verification has a shelf life: the fact
    # it was checked against can move afterwards without anything noticing.
    at: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.how in ("verified", "stated")

    def as_dict(self) -> dict:
        return {"target": self.target, "how": self.how, "at": self.at}


@dataclass(frozen=True)
class Node:
    """One declared unit: a piece of work, or a contract between pieces."""

    id: str
    kind: str
    status: str
    title: str = ""
    parent: str | None = None
    # (gate_name, expression_source) pairs, sorted for stable hashing.
    gates: tuple[tuple[str, str], ...] = ()
    # Capability tags this node publishes once it is done.
    provides: tuple[str, ...] = ()
    # Contract only: the interface version consumers pin against.
    version: int | None = None
    # Contract only: the work nodes that implement it.
    satisfied_by: tuple[str, ...] = ()
    # Named facts this node publishes to the rest of the graph. Values are gate
    # expressions (or literals). This is the encapsulation boundary: outsiders
    # gate on a published fact instead of reaching inside for the internals.
    publishes: tuple[tuple[str, object], ...] = ()
    # Provenance for this node's outgoing edges, keyed by referenced node.
    evidence: tuple[EdgeEvidence, ...] = ()
    # Findings this node has answered for good. An accurate observation that
    # will never change is noise on the second run, and noise is how a whole
    # severity gets ignored. Acknowledged findings are counted, never hidden.
    acknowledge: tuple[str, ...] = ()
    notes: str = ""
    # Where this came from. Deliberately excluded from the fingerprint.
    source: str = ""

    @property
    def gate_map(self) -> dict[str, str]:
        return dict(self.gates)

    def gate(self, name: str) -> str | None:
        return self.gate_map.get(name)

    @property
    def publishes_map(self) -> dict[str, object]:
        return dict(self.publishes)

    @property
    def evidence_map(self) -> dict[str, EdgeEvidence]:
        return {e.target: e for e in self.evidence}

    def acknowledges(self, code: str) -> bool:
        return code in self.acknowledge

    def fingerprint(self) -> str:
        """Hash of the semantic declared fields.

        Excludes `title`, `notes`, `source`, `evidence`, and `acknowledge`: none
        of them can
        change a derived value. Provenance in particular affects only how a
        result is *reported*, so annotating an edge must not invalidate a cache
        entry — otherwise nobody would annotate anything.
        """
        payload = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "parent": self.parent,
            "gates": [list(g) for g in self.gates],
            "provides": list(self.provides),
            "version": self.version,
            "satisfied_by": list(self.satisfied_by),
            "publishes": [list(p) for p in self.publishes],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def node_from_dict(data: dict, source: str = "") -> Node:
    if not isinstance(data, dict):
        raise ModelError(f"{source}: expected a mapping, got {type(data).__name__}")

    unknown = set(data) - {
        "id",
        "kind",
        "title",
        "status",
        "parent",
        "gates",
        "provides",
        "version",
        "satisfied_by",
        "publishes",
        "evidence",
        "acknowledge",
        "notes",
    }
    if unknown:
        raise ModelError(f"{source}: unknown field(s): {', '.join(sorted(unknown))}")

    node_id = data.get("id")
    if not node_id or not isinstance(node_id, str):
        raise ModelError(f"{source}: node needs a string `id`")

    kind = data.get("kind", "work")
    if kind not in KINDS:
        raise ModelError(f"{node_id}: kind must be one of {', '.join(KINDS)}")

    valid_statuses = WORK_STATUSES if kind == "work" else CONTRACT_STATUSES
    default_status = "not_started" if kind == "work" else "draft"
    status = data.get("status", default_status)
    if status not in valid_statuses:
        raise ModelError(
            f"{node_id}: status {status!r} invalid for kind {kind!r}; "
            f"expected one of {', '.join(valid_statuses)}"
        )

    raw_gates = data.get("gates") or {}
    if not isinstance(raw_gates, dict):
        raise ModelError(f"{node_id}: `gates` must be a mapping of name -> expression")
    for name, expr in raw_gates.items():
        if not isinstance(expr, str):
            raise ModelError(f"{node_id}: gate {name!r} must be an expression string")
    gates = tuple(sorted((str(k), v.strip()) for k, v in raw_gates.items()))

    raw_publishes = data.get("publishes") or {}
    if not isinstance(raw_publishes, dict):
        raise ModelError(
            f"{node_id}: `publishes` must be a mapping of name -> expression"
        )
    for name, value in raw_publishes.items():
        if name in RESERVED_EXPORTS:
            raise ModelError(
                f"{node_id}: cannot publish {name!r} - it would shadow the built-in "
                f"export of the same name"
            )
        if not isinstance(value, (str, int, bool)):
            raise ModelError(
                f"{node_id}: published fact {name!r} must be an expression string "
                f"or a literal number/boolean"
            )
    publishes = tuple(
        sorted(
            (str(k), v.strip() if isinstance(v, str) else v)
            for k, v in raw_publishes.items()
        )
    )

    raw_evidence = data.get("evidence") or {}
    if not isinstance(raw_evidence, dict):
        raise ModelError(
            f"{node_id}: `evidence` must be a mapping of node id -> how/at"
        )
    edge_evidence = []
    for target, spec in raw_evidence.items():
        if isinstance(spec, str):
            spec = {"how": spec}  # shorthand: `agent.plan: verified`
        if not isinstance(spec, dict):
            raise ModelError(
                f"{node_id}: evidence for {target!r} must be a string or a mapping"
            )
        how = spec.get("how")
        if how not in HOW:
            raise ModelError(
                f"{node_id}: evidence for {target!r} has how={how!r}; "
                f"expected one of {', '.join(HOW)}"
            )
        at = spec.get("at")
        if at is not None:
            at = str(at)
        edge_evidence.append(EdgeEvidence(target=str(target), how=how, at=at))
    edge_evidence_t = tuple(sorted(edge_evidence, key=lambda e: e.target))

    raw_ack = data.get("acknowledge") or []
    if isinstance(raw_ack, str):
        raw_ack = [raw_ack]
    if not isinstance(raw_ack, list):
        raise ModelError(f"{node_id}: `acknowledge` must be a list of finding codes")
    acknowledge = tuple(sorted(str(a) for a in raw_ack))

    provides = tuple(sorted(str(p) for p in (data.get("provides") or [])))
    satisfied_by = tuple(sorted(str(s) for s in (data.get("satisfied_by") or [])))

    version = data.get("version")
    if version is not None and not isinstance(version, int):
        raise ModelError(f"{node_id}: `version` must be an integer")
    if kind == "work" and satisfied_by:
        raise ModelError(f"{node_id}: `satisfied_by` only applies to contract nodes")

    return Node(
        id=node_id,
        kind=kind,
        status=status,
        title=str(data.get("title") or node_id),
        parent=data.get("parent"),
        gates=gates,
        provides=provides,
        version=version,
        satisfied_by=satisfied_by,
        publishes=publishes,
        evidence=edge_evidence_t,
        acknowledge=acknowledge,
        notes=str(data.get("notes") or ""),
        source=source,
    )


class Graph:
    """An immutable collection of declared nodes, plus the indexes over them.

    The dependency edges are *derived from the gate expressions*, never
    declared separately. A `depends_on:` list maintained by hand would drift
    out of sync with the requirements that actually matter; extracting the
    references from the expression means the two cannot disagree.
    """

    def __init__(self, nodes: dict[str, Node]):
        self.nodes = nodes
        self._children: dict[str, list[str]] = {nid: [] for nid in nodes}
        for nid, node in nodes.items():
            if node.parent:
                self._children.setdefault(node.parent, []).append(nid)
        for kids in self._children.values():
            kids.sort()
        self._deps_cache: dict[str, tuple[str, ...]] = {}
        self._ref_cache: dict[str, tuple[str, ...]] = {}

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def __iter__(self):
        return iter(self.nodes.values())

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise KeyError(f"unknown node {node_id!r}") from None

    def ids(self) -> list[str]:
        return sorted(self.nodes)

    def children_of(self, node_id: str) -> tuple[str, ...]:
        return tuple(self._children.get(node_id, ()))

    def descendants_of(self, node_id: str) -> tuple[str, ...]:
        out: list[str] = []
        stack = list(self.children_of(node_id))
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(self.children_of(cur))
        return tuple(sorted(out))

    def resolve_ref(self, dotted: str) -> tuple[str, tuple[str, ...]]:
        """Split `a.b.done` into the longest matching node id plus the rest.

        Longest-prefix wins, so adding a node `a.b.retry` later never changes
        how `a.b.done` resolves. The loader rejects ids ending in a reserved
        export name, which is the only way this could become ambiguous.
        """
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            candidate = ".".join(parts[:cut])
            if candidate in self.nodes:
                return candidate, tuple(parts[cut:])
        raise KeyError(f"reference {dotted!r} does not name a known node")

    def references_of(self, node_id: str) -> tuple[str, ...]:
        """Node ids referenced by this node's gate expressions."""
        if node_id in self._ref_cache:
            return self._ref_cache[node_id]

        from . import expr as expr_mod

        node = self.get(node_id)
        found: set[str] = set()
        sources = [src for _, src in node.gates]
        sources += [v for _, v in node.publishes if isinstance(v, str)]
        for source in sources:
            for dotted in expr_mod.references(source):
                try:
                    target, _rest = self.resolve_ref(dotted)
                except KeyError:
                    # Dangling refs are reported by `check`, not raised here —
                    # a half-written graph should still be queryable.
                    continue
                if target != node_id:
                    found.add(target)
        # A contract depends on whoever implements it.
        for impl in node.satisfied_by:
            if impl in self.nodes:
                found.add(impl)
        result = tuple(sorted(found))
        self._ref_cache[node_id] = result
        return result

    def dependencies_of(self, node_id: str) -> tuple[str, ...]:
        """Everything whose exports feed this node's derived state.

        That is: gate references, contract implementers, and — for a parent —
        its children, since rollups read upward from them.
        """
        if node_id in self._deps_cache:
            return self._deps_cache[node_id]
        deps = set(self.references_of(node_id)) | set(self.children_of(node_id))
        deps.discard(node_id)
        result = tuple(sorted(deps))
        self._deps_cache[node_id] = result
        return result

    def dependents_of(self, node_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(n for n in self.nodes if node_id in self.dependencies_of(n))
        )

    def referrers_of(self, node_id: str) -> tuple[str, ...]:
        """Nodes that name this one in a gate, a published fact, or satisfied_by.

        Narrower than `dependents_of`, which also counts a parent reading its
        own children. Containment is not a requirement: a node can sit in a
        hierarchy while requiring nothing and being required by nothing.
        """
        return tuple(sorted(n for n in self.nodes if node_id in self.references_of(n)))

    def with_overlay(self, overlay: dict[str, dict]) -> Graph:
        """A copy of this graph with per-node declared fields replaced.

        Used by what-if queries. Nodes outside the overlay keep their exact
        fingerprints, so their cache entries stay valid across the two graphs.
        """
        nodes = dict(self.nodes)
        for node_id, changes in overlay.items():
            base = self.get(node_id)
            data = {
                "id": base.id,
                "kind": base.kind,
                "title": base.title,
                "status": base.status,
                "parent": base.parent,
                "gates": base.gate_map,
                "provides": list(base.provides),
                "version": base.version,
                "satisfied_by": list(base.satisfied_by),
                "publishes": base.publishes_map,
                "evidence": {
                    e.target: {"how": e.how, "at": e.at} for e in base.evidence
                },
                "acknowledge": list(base.acknowledge),
                "notes": base.notes,
            }
            if base.kind == "work":
                data.pop("satisfied_by")
            data.update(changes)
            nodes[node_id] = node_from_dict(data, source=base.source)
        return Graph(nodes)
