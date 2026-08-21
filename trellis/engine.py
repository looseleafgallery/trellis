"""The incremental evaluator.

Every node's derived state is a pure function of two things: its own declared
fields, and the *exports* of its dependencies. Nothing else. That constraint is
what makes the whole thing cacheable — the cache key is literally a hash of
those inputs, so a hit is exact by construction and there is no invalidation
pass to get wrong.

Exports are deliberately coarser than derived state. A node publishes whether
it is done, what it provides, how far along its subtree is — not its full
record. This produces *early cutoff*: moving a stage from `not_started` to
`in_progress` changes its derived state but usually not its exports, so its
dependents' cache keys are unchanged and the walk stops one hop out. In a
pipeline where subsystems are chained deep, most changes die immediately.
That, more than the memo store, is where the efficiency comes from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import expr as expr_mod
from .cache import Cache
from .model import Graph, Node

ENGINE_VERSION = 7

# Work readiness values, ordered from least to most progressed.
READINESS = (
    "blocked",
    # Not blocked by work: the gate is open and a person owes a decision. The
    # same distinction contracts already draw between `unagreed` and `pending`.
    "awaiting",
    "ready",
    "active",
    "unverified",
    "done",
    "superseded",
    "abandoned",
)
# Contracts answer a different question — what is this waiting on? `unagreed`
# waits on people, `pending` waits on implementation work. Collapsing the two
# would hide which kind of push a stuck pipeline actually needs.
CONTRACT_READINESS = ("unagreed", "pending", "live")


class CycleError(RuntimeError):
    """The dependency graph is not a DAG."""

    def __init__(self, path: list[str]):
        self.path = path
        super().__init__("dependency cycle: " + " -> ".join(path))


@dataclass
class Gate:
    name: str
    expr: str
    satisfied: bool
    unmet: tuple[dict, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "expr": self.expr,
            "satisfied": self.satisfied,
            "unmet": [dict(u) for u in self.unmet],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Gate:
        return cls(
            name=data["name"],
            expr=data["expr"],
            satisfied=data["satisfied"],
            unmet=tuple(data.get("unmet") or ()),
            error=data.get("error"),
        )


@dataclass
class Derived:
    """Everything computed about one node. Cacheable, display-agnostic."""

    id: str
    kind: str
    status: str
    readiness: str
    gates: dict[str, Gate] = field(default_factory=dict)
    violations: tuple[dict, ...] = ()
    exports: dict = field(default_factory=dict)
    exports_hash: str = ""
    # Runtime-only bookkeeping; never hashed, never persisted.
    key: str = ""
    cached: bool = False

    @property
    def start_gate(self) -> Gate | None:
        return self.gates.get("start")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "readiness": self.readiness,
            "gates": {k: g.as_dict() for k, g in self.gates.items()},
            "violations": [dict(v) for v in self.violations],
            "exports": self.exports,
            "exports_hash": self.exports_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Derived:
        return cls(
            id=data["id"],
            kind=data["kind"],
            status=data["status"],
            readiness=data["readiness"],
            gates={k: Gate.from_dict(v) for k, v in (data.get("gates") or {}).items()},
            violations=tuple(data.get("violations") or ()),
            exports=data.get("exports") or {},
            exports_hash=data.get("exports_hash", ""),
        )


def _hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def cache_key(node: Node, dep_hashes: dict[str, str]) -> str:
    """Hash of (evaluator version, declared fields, dependency exports).

    Dependencies contribute only their `exports_hash`, never their full derived
    record — that is what gives early cutoff.
    """
    return _hash(
        {
            "v": ENGINE_VERSION,
            "node": node.fingerprint(),
            "deps": sorted(dep_hashes.items()),
        }
    )


class Engine:
    """Demand-driven, memoized evaluation over a Graph."""

    def __init__(self, graph: Graph, cache: Cache | None = None):
        self.graph = graph
        self.cache = cache if cache is not None else Cache()
        self._memo: dict[str, Derived] = {}
        self._stack: list[str] = []
        self.recomputed: set[str] = set()
        self.reused: set[str] = set()

    # -- public API ---------------------------------------------------------

    def derived(self, node_id: str) -> Derived:
        if node_id in self._memo:
            return self._memo[node_id]
        if node_id in self._stack:
            cycle = [*self._stack[self._stack.index(node_id) :], node_id]
            raise CycleError(cycle)

        node = self.graph.get(node_id)
        self._stack.append(node_id)
        try:
            deps = {d: self.derived(d) for d in self.graph.dependencies_of(node_id)}
        finally:
            self._stack.pop()

        key = cache_key(node, {d: v.exports_hash for d, v in deps.items()})
        hit = self.cache.get(key)
        if hit is not None:
            result = Derived.from_dict(hit)
            self.reused.add(node_id)
        else:
            result = self._compute(node, deps)
            self.cache.put(key, result.as_dict())
            self.recomputed.add(node_id)
        result.key = key
        result.cached = hit is not None
        self._memo[node_id] = result
        return result

    def all_derived(self) -> dict[str, Derived]:
        return {nid: self.derived(nid) for nid in self.graph.ids()}

    # -- computation --------------------------------------------------------

    def _resolver(self, node: Node, deps: dict[str, Derived]):
        def resolve(dotted: str) -> Any:
            try:
                target, rest = self.graph.resolve_ref(dotted)
            except KeyError as exc:
                raise expr_mod.ExprError(str(exc)) from None
            if target == node.id:
                raise expr_mod.ExprError(f"gate on {node.id!r} references its own node")
            dep = deps.get(target)
            if dep is None:
                raise expr_mod.ExprError(f"{dotted}: {target!r} is not a dependency")
            value: Any = dep.exports
            walked = target
            for part in rest:
                if not isinstance(value, dict) or part not in value:
                    raise expr_mod.ExprError(
                        f"{walked!r} has no export {part!r} "
                        f"(available: {', '.join(sorted(dep.exports))})"
                    )
                value = value[part]
                walked = f"{walked}.{part}"
            return value

        return resolve

    def _eval_gates(self, node: Node, deps: dict[str, Derived]) -> dict[str, Gate]:
        resolve = self._resolver(node, deps)
        gates: dict[str, Gate] = {}
        for name, source in node.gates:
            try:
                trace = expr_mod.evaluate(source, resolve)
            except expr_mod.ExprError as exc:
                gates[name] = Gate(name, source, satisfied=False, error=str(exc))
                continue
            unmet = tuple({"src": t.src, "value": t.value} for t in trace.unmet())
            gates[name] = Gate(name, source, satisfied=bool(trace.value), unmet=unmet)
        return gates

    def _eval_publishes(
        self, node: Node, deps: dict[str, Derived]
    ) -> tuple[dict, list[dict]]:
        """Named facts a node offers the rest of the graph.

        A published fact is the encapsulation boundary: consumers gate on it
        instead of reaching inside for the internals, so the internals stay
        free to change. It is also good for early cutoff — a fact summarizing a
        subsystem flips far less often than the pieces it summarizes.
        """
        if not node.publishes:
            return {}, []
        resolve = self._resolver(node, deps)
        published: dict = {}
        violations: list[dict] = []
        for name, source in node.publishes:
            if not isinstance(source, str):
                published[name] = source  # a literal, not an expression
                continue
            try:
                published[name] = expr_mod.evaluate(source, resolve).value
            except expr_mod.ExprError as exc:
                # Omit the fact rather than publishing a wrong value; anything
                # gating on it gets a clear "no such export" error in turn.
                violations.append(
                    {
                        "code": "publish_error",
                        "severity": "error",
                        "message": f"published fact {name!r} could not be evaluated: {exc}",
                    }
                )
        return published, violations

    def _compute(self, node: Node, deps: dict[str, Derived]) -> Derived:
        gates = self._eval_gates(node, deps)
        if node.kind == "contract":
            exports, readiness, violations = self._contract_state(node, deps, gates)
        else:
            exports, readiness, violations = self._work_state(node, deps, gates)

        published, publish_violations = self._eval_publishes(node, deps)
        exports.update(published)
        violations += publish_violations
        violations += self._dependency_violations(node, deps)
        return Derived(
            id=node.id,
            kind=node.kind,
            status=node.status,
            readiness=readiness,
            gates=gates,
            violations=tuple(violations),
            exports=exports,
            exports_hash=_hash(exports),
        )

    def _work_state(
        self, node: Node, deps: dict[str, Derived], gates: dict[str, Gate]
    ) -> tuple[dict, str, list[dict]]:
        children = self.graph.children_of(node.id)
        done = node.status == "done"
        unverified = node.status == "done_unverified"
        abandoned = node.status == "abandoned"
        superseded = node.status == "superseded"
        dead = abandoned or superseded

        if children:
            leaf_total = sum(deps[c].exports.get("leaf_total", 1) for c in children)
            leaf_done = sum(deps[c].exports.get("leaf_done", 0) for c in children)
            children_done = all(deps[c].exports.get("done") for c in children)
        else:
            # Dead work leaves the denominator entirely; unverified work counts
            # toward neither side, which understates rather than overstates.
            leaf_total = 0 if dead else 1
            leaf_done = 1 if done else 0
            children_done = True

        exports = {
            "done": done,
            # True for done *and* done_unverified: the work exists, the check
            # of it may not.
            "complete": done or unverified,
            "active": node.status == "in_progress",
            "abandoned": abandoned,
            "superseded": superseded,
            "dead": dead,
            "provides": list(node.provides) if done else [],
            "awaiting": bool(node.awaiting),
            "children_done": children_done,
            "leaf_total": leaf_total,
            "leaf_done": leaf_done,
            "progress": round(leaf_done / leaf_total, 4) if leaf_total else 1.0,
        }

        start = gates.get("start")
        finish = gates.get("finish")
        start_ok = start.satisfied if start else True

        if abandoned:
            readiness = "abandoned"
        elif superseded:
            readiness = "superseded"
        elif unverified:
            readiness = "unverified"
        elif done:
            readiness = "done"
        elif node.status == "in_progress":
            readiness = "active"
        elif start_ok and node.awaiting:
            # Blocked-by-work outranks this: if the gate is shut, the work is
            # the truth. This is "would be ready except a person owes something".
            readiness = "awaiting"
        elif start_ok:
            readiness = "ready"
        else:
            readiness = "blocked"

        violations: list[dict] = []
        for gate in gates.values():
            if gate.error:
                violations.append(
                    {
                        "code": "gate_error",
                        "severity": "error",
                        "message": f"gate {gate.name!r} could not be "
                        f"evaluated: {gate.error}",
                    }
                )
        if node.status == "in_progress" and not start_ok:
            violations.append(
                {
                    "code": "working_ahead",
                    "severity": "warn",
                    "message": "in progress, but its start gate is not satisfied",
                }
            )
        if (done or unverified) and not start_ok:
            violations.append(
                {
                    "code": "gate_bypassed",
                    "severity": "error",
                    "message": "marked done, but its start gate was never satisfied",
                }
            )
        if done and finish is not None and not finish.satisfied:
            violations.append(
                {
                    "code": "gate_bypassed",
                    "severity": "error",
                    "message": "marked done, but its finish gate is not satisfied",
                }
            )
        if (done or unverified) and children and not children_done:
            violations.append(
                {
                    "code": "parent_ahead_of_children",
                    "severity": "error",
                    "message": "marked done while children are still open",
                }
            )
        if not done and not dead and children and children_done:
            violations.append(
                {
                    "code": "rollup_lagging",
                    "severity": "info",
                    "message": "every child is done; this can probably be closed",
                }
            )
        return exports, readiness, violations

    def _contract_state(
        self, node: Node, deps: dict[str, Derived], gates: dict[str, Gate]
    ) -> tuple[dict, str, list[dict]]:
        implementers = [d for d in node.satisfied_by if d in deps]
        implemented = all(deps[d].exports.get("done") for d in implementers)
        agreed = node.status in ("agreed", "frozen")
        gate_ok = all(g.satisfied for g in gates.values())
        live = agreed and implemented and bool(implementers) and gate_ok

        exports = {
            # `done` mirrors `live` so all_done() reads uniformly across kinds.
            "done": live,
            "live": live,
            "agreed": agreed,
            "frozen": node.status == "frozen",
            "version": node.version if node.version is not None else 0,
            "provides": list(node.provides) if live else [],
            "leaf_total": 0,
            "leaf_done": 0,
            "progress": 1.0 if live else 0.0,
        }
        readiness = "live" if live else ("pending" if agreed else "unagreed")

        violations: list[dict] = []
        for gate in gates.values():
            if gate.error:
                violations.append(
                    {
                        "code": "gate_error",
                        "severity": "error",
                        "message": f"gate {gate.name!r} could not be "
                        f"evaluated: {gate.error}",
                    }
                )
        if node.status == "frozen" and not implemented:
            missing = [d for d in implementers if not deps[d].exports.get("done")]
            violations.append(
                {
                    "code": "frozen_unimplemented",
                    "severity": "error",
                    "message": "frozen, but not yet implemented by: "
                    + ", ".join(missing),
                }
            )
        if agreed and not implementers:
            violations.append(
                {
                    "code": "orphan_contract",
                    "severity": "warn",
                    "message": "agreed, but nothing claims to implement it",
                }
            )
        return exports, readiness, violations

    def _dependency_violations(
        self, node: Node, deps: dict[str, Derived]
    ) -> tuple[dict, ...]:
        """Checks that read only dependency exports, so they stay cacheable."""
        if node.status in ("abandoned", "superseded", "done"):
            return ()
        gone = [
            ref
            for ref in self.graph.references_of(node.id)
            if deps.get(ref) and deps[ref].exports.get("dead")
        ]
        if not gone:
            return ()
        return (
            {
                "code": "depends_on_abandoned",
                "severity": "warn",
                "message": "requires abandoned or superseded node(s): "
                + ", ".join(sorted(gone)),
            },
        )
