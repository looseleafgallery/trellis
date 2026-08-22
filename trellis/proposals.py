"""Proposals that outlive the session that made them.

An agent models and a person decides, and they are almost never at the keyboard
at the same time. Without somewhere to put it, "something proposed this and
nobody has decided yet" lives in prose in a second system — which is a second
place to be wrong.

Stored beside the journal, and committed for the same reason: a proposal
awaiting a decision is a handoff between two parties, so it cannot live
somewhere the other party cannot see.

Append-only. An accept or a reject is a **later record**, never an edit to the
proposal — the same rule the journal follows, and what makes "this was rejected
in March, and here is why" answerable at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .delta import Delta
from .journal import HISTORY_DIRNAME
from .loader import project_root
from .model import Graph

PROPOSALS_NAME = "proposals.jsonl"

# How long a proposal may sit before the trust layer starts asking about it.
# Three weeks, from the issue: long enough that a normal review cycle does not
# trip it, short enough that a forgotten one surfaces while the context that
# produced it can still be recovered.
STALE_AFTER_DAYS = 21


def proposals_path(graph_dir: str | Path) -> Path:
    return project_root(graph_dir) / HISTORY_DIRNAME / PROPOSALS_NAME


@dataclass
class Proposal:
    """One proposed change, and what was true when it was proposed."""

    id: str
    key: str
    at: str
    delta: Delta
    origin: str = ""
    text: str = ""
    why: str = ""
    by: str = ""
    # The fingerprint of every node the delta touches, as it was at propose
    # time. Semantic fields only — a reworded note does not invalidate a
    # pending decision, and would be an infuriating way to lose one.
    fingerprints: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": "proposed",
            "id": self.id,
            "key": self.key,
            "at": self.at,
            "origin": self.origin,
            "text": self.text,
            "why": self.why,
            "by": self.by,
            "fingerprints": self.fingerprints,
            "delta": self.delta.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        return cls(
            id=data["id"],
            key=data.get("key", ""),
            at=data.get("at", ""),
            delta=Delta.from_dict(data.get("delta") or {}),
            origin=data.get("origin", ""),
            text=data.get("text", ""),
            why=data.get("why", ""),
            by=data.get("by", ""),
            fingerprints=dict(data.get("fingerprints") or {}),
        )

    def nodes(self) -> list[str]:
        """Every node this proposal touches, in a stable order."""
        touched = {c.node for c in self.delta.changes}
        touched |= {s.get("id", "") for s in self.delta.new_nodes if s.get("id")}
        return sorted(touched)

    def age_days(self) -> int | None:
        return _age_days(self.at)


@dataclass
class Decision:
    """What someone decided about a proposal, and why."""

    id: str
    kind: str  # accepted | rejected
    at: str
    reason: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "at": self.at, "reason": self.reason}


def _age_days(stamp: str) -> int | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).days


def content_key(delta: Delta) -> str:
    """A hash of what the delta actually does.

    Identity by content, not by handle, so the same proposal arriving a second
    time can find its own prior rejection. Without this, a rejected proposal
    comes back monthly and nothing remembers saying no.
    """
    payload = {
        "changes": sorted((c.node, c.field, str(c.value)) for c in delta.changes),
        "new_nodes": sorted(
            json.dumps(spec, sort_keys=True, default=str) for spec in delta.new_nodes
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def fingerprints_for(graph: Graph, delta: Delta) -> dict[str, str]:
    """What the touched nodes looked like when this was proposed.

    A node the delta *creates* has no fingerprint yet; its absence is the thing
    being asserted, and `validate` already refuses to create one that exists.
    """
    out: dict[str, str] = {}
    for change in delta.changes:
        if change.node in graph:
            out[change.node] = graph.get(change.node).fingerprint()
    return out


def read(graph_dir: str | Path) -> list[dict]:
    """Every record, oldest first."""
    path = proposals_path(graph_dir)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # A corrupt line loses that record, not the file. Silently dropping
            # the rest would turn one bad write into an empty queue.
            continue
    return out


def _append(graph_dir: str | Path, record: dict) -> Path:
    path = proposals_path(graph_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def next_id(graph_dir: str | Path) -> str:
    """Sequential handles, never reused.

    Numbered over every proposal ever made rather than over the pending ones,
    so a handle in a comment three months old still names the same thing.
    """
    used = sum(1 for r in read(graph_dir) if r.get("kind") == "proposed")
    return f"p{used + 1}"


def propose(
    graph_dir: str | Path,
    graph: Graph,
    delta: Delta,
    origin: str = "",
    text: str = "",
    why: str = "",
    by: str = "",
) -> Proposal:
    proposal = Proposal(
        id=next_id(graph_dir),
        key=content_key(delta),
        at=_now(),
        delta=delta,
        origin=origin,
        text=text,
        why=why,
        by=by,
        fingerprints=fingerprints_for(graph, delta),
    )
    _append(graph_dir, proposal.as_dict())
    return proposal


def decide(graph_dir: str | Path, proposal_id: str, kind: str, reason: str) -> Decision:
    decision = Decision(id=proposal_id, kind=kind, at=_now(), reason=reason)
    _append(graph_dir, decision.as_dict())
    return decision


def decisions(graph_dir: str | Path) -> dict[str, Decision]:
    """The decision on each proposal, latest wins."""
    out: dict[str, Decision] = {}
    for record in read(graph_dir):
        if record.get("kind") in ("accepted", "rejected"):
            out[record["id"]] = Decision(
                id=record["id"],
                kind=record["kind"],
                at=record.get("at", ""),
                reason=record.get("reason", "") or "",
            )
    return out


def all_proposals(graph_dir: str | Path) -> list[Proposal]:
    return [
        Proposal.from_dict(r) for r in read(graph_dir) if r.get("kind") == "proposed"
    ]


def pending(graph_dir: str | Path) -> list[Proposal]:
    """Proposed and not yet decided, oldest first."""
    decided = decisions(graph_dir)
    return [p for p in all_proposals(graph_dir) if p.id not in decided]


def get(graph_dir: str | Path, proposal_id: str) -> Proposal | None:
    for proposal in all_proposals(graph_dir):
        if proposal.id == proposal_id:
            return proposal
    return None


def rejected_before(
    graph_dir: str | Path, key: str
) -> tuple[Proposal, Decision] | None:
    """The last time this exact change was proposed and turned down.

    Answering "we said no to this in March, because X" is the whole reason
    rejections are kept rather than deleted.
    """
    decided = decisions(graph_dir)
    for proposal in reversed(all_proposals(graph_dir)):
        if proposal.key != key:
            continue
        decision = decided.get(proposal.id)
        if decision and decision.kind == "rejected":
            return proposal, decision
    return None


def moved(graph: Graph, proposal: Proposal) -> list[str]:
    """Nodes that are not what they were when this was proposed.

    This is the *identity* question, and it is the one a fingerprint can answer
    exactly: is the thing you proposed against still the thing that is there.
    It is deliberately not the same question as whether the proposal's
    consequences changed — that is recomputed at accept time and re-shown,
    because a graph that moves around a still-applicable proposal is normal and
    refusing there would make the queue unusable.
    """
    out = []
    for node_id, before in sorted(proposal.fingerprints.items()):
        if node_id not in graph:
            out.append(node_id)
            continue
        if graph.get(node_id).fingerprint() != before:
            out.append(node_id)
    return out


def stale(graph_dir: str | Path, after_days: int = STALE_AFTER_DAYS) -> list[Proposal]:
    """Pending proposals old enough to be worth asking about.

    A queue nobody empties is a worse place for a decision than the prose it
    replaced, because it looks like it is being handled.
    """
    out = []
    for proposal in pending(graph_dir):
        age = proposal.age_days()
        if age is not None and age >= after_days:
            out.append(proposal)
    return out
