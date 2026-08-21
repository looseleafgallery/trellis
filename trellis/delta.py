"""A proposed change to the declared graph.

A delta is the unit that flows through the write loop: something proposes one
(you, on the command line, or a model reading your prose), it is validated
against the model rules, previewed with `queries.impact`, and only then written.

The preview is the same code path as the what-if query — `Delta.overlay()`
produces exactly the overlay dict `impact` already understands. A proposal you
are about to apply and a hypothetical you are just asking about are the same
object, which is why the preview cannot drift from what actually lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Graph, ModelError, node_from_dict

# Fields the writer can safely edit in place. All scalars: a structural change
# (rewriting a `gates:` block) is a YAML surgery problem, not a state update,
# and belongs in your editor. See edit.py.
EDITABLE_FIELDS = ("status", "version", "title", "parent")


class DeltaError(ValueError):
    """A proposed change is not applicable to this graph."""


@dataclass
class ProposedChange:
    node: str
    field: str
    value: object
    why: str = ""
    # 1.0 for anything you typed yourself; model proposals carry their own.
    confidence: float = 1.0

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "field": self.field,
            "value": self.value,
            "why": self.why,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProposedChange:
        return cls(
            node=data["node"],
            field=data["field"],
            value=data["value"],
            why=data.get("why", ""),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass
class Delta:
    changes: list[ProposedChange] = field(default_factory=list)
    # Whole nodes to create, as declared-node mappings.
    new_nodes: list[dict] = field(default_factory=list)
    # Things the proposer could not map onto the graph. Surfaced, never guessed at.
    unmatched: list[str] = field(default_factory=list)
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.changes or self.new_nodes)

    def overlay(self) -> dict[str, dict]:
        """The same shape `queries.impact` takes, so preview == apply."""
        out: dict[str, dict] = {}
        for change in self.changes:
            out.setdefault(change.node, {})[change.field] = change.value
        return out

    def as_dict(self) -> dict:
        return {
            "changes": [c.as_dict() for c in self.changes],
            "new_nodes": self.new_nodes,
            "unmatched": self.unmatched,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Delta:
        return cls(
            changes=[ProposedChange.from_dict(c) for c in data.get("changes") or []],
            new_nodes=list(data.get("new_nodes") or []),
            unmatched=list(data.get("unmatched") or []),
            source=data.get("source", ""),
        )


def coerce(field_name: str, value: object) -> object:
    """Normalize a proposed value to the type the model layer expects."""
    if field_name == "version" and isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise DeltaError(f"version must be an integer, got {value!r}") from None
    if field_name == "parent" and value in ("", "none", "null", None):
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def validate(delta: Delta, graph: Graph) -> list[str]:
    """Every reason this delta cannot be applied. Empty means it is safe.

    Validation runs the real constructors rather than re-deriving the rules,
    so an invalid status or a bad field can only be wrong in one place.
    """
    problems: list[str] = []
    new_ids = set()

    for spec in delta.new_nodes:
        node_id = spec.get("id")
        if not node_id:
            problems.append("new node is missing an `id`")
            continue
        if node_id in graph:
            problems.append(
                f"{node_id}: already exists; change it instead of creating it"
            )
            continue
        if node_id in new_ids:
            problems.append(f"{node_id}: proposed twice")
            continue
        new_ids.add(node_id)
        try:
            node_from_dict(spec)
        except ModelError as exc:
            problems.append(str(exc))
            continue
        parent = spec.get("parent")
        if parent and parent not in graph and parent not in new_ids:
            problems.append(f"{node_id}: parent {parent!r} does not exist")

    for change in delta.changes:
        if change.node not in graph and change.node not in new_ids:
            problems.append(f"{change.node}: no such node")
            continue
        if change.field not in EDITABLE_FIELDS:
            problems.append(
                f"{change.node}: {change.field!r} is not writable "
                f"(writable: {', '.join(EDITABLE_FIELDS)})"
            )
            continue
        if change.node in graph:
            node = graph.get(change.node)
            if change.field == "version" and node.kind != "contract":
                problems.append(f"{change.node}: only contracts have a version")

    if problems:
        return problems

    # Final gate: build the graph the change would produce. Anything the model
    # layer rejects (an invalid status for the kind, say) surfaces here.
    try:
        graph.with_overlay(delta.overlay())
    except ModelError as exc:
        problems.append(str(exc))
    return problems


def normalize(delta: Delta) -> Delta:
    """Coerce values and drop no-op fields, leaving the delta ready to validate."""
    delta.changes = [
        ProposedChange(c.node, c.field, coerce(c.field, c.value), c.why, c.confidence)
        for c in delta.changes
    ]
    return delta


def drop_noops(delta: Delta, graph: Graph) -> Delta:
    """Remove changes that would not alter anything.

    A proposal restating what is already declared is not wrong, just empty —
    and letting it through would produce a confirmation prompt for a write that
    changes no bytes.
    """
    kept: list[ProposedChange] = []
    for change in delta.changes:
        if change.node not in graph:
            kept.append(change)
            continue
        current = getattr(graph.get(change.node), change.field, None)
        if current != change.value:
            kept.append(change)
    delta.changes = kept
    return delta
