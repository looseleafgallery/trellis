"""Turning prose into a proposed delta.

This is the only module that calls a model, and it is deliberately the thinnest
possible use of one: it maps a sentence onto node ids and status values, and
stops. It does not decide what the change unblocks, whether a gate is now
satisfied, or what state the system ends up in — all of that is computed by the
engine, exactly as it is for a change you type by hand. A model that is only
asked to do the part requiring language cannot be wrong about the part
requiring logic.

Nothing here writes. The Delta returned is validated, previewed, and confirmed
before a byte moves; see cli.py.

The `anthropic` package is an optional dependency — the core engine never
imports this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .cache import Cache
from .delta import Delta, ProposedChange, drop_noops, normalize
from .model import CONTRACT_STATUSES, WORK_STATUSES, Graph

if TYPE_CHECKING:
    from .engine import Engine

MODEL = "claude-opus-5"
MAX_TOKENS = 8000
# Bump when the prompt or context format changes: it is part of the cache key,
# so a stale proposal can never survive a change to how proposals are made.
PROMPT_VERSION = 1
# Above this many nodes, send only the ones the text plausibly refers to.
CONTEXT_LIMIT = 60


class MissingCredentialsError(RuntimeError):
    """No Anthropic credentials are configured."""


class ProposalError(RuntimeError):
    """The model call failed or returned something unusable."""


class _Change(BaseModel):
    node: str = Field(description="The exact id of an existing node, copied verbatim.")
    field: str = Field(
        description="One of: status, version, title, parent. Almost always status."
    )
    value: str = Field(
        description=(
            "The new value. For work nodes status is one of: "
            + ", ".join(WORK_STATUSES)
            + ". For contract nodes status is one of: "
            + ", ".join(CONTRACT_STATUSES)
            + "."
        )
    )
    why: str = Field(
        description="The words in the input that justify this change. One short phrase."
    )
    confidence: float = Field(
        description=(
            "0.0 to 1.0. Use below 0.7 when the mapping onto this node is a guess "
            "rather than something the text states."
        )
    )


class _NewNode(BaseModel):
    id: str = Field(description="Dotted id, consistent with the existing naming.")
    title: str
    kind: str = Field(description="`work` or `contract`.")
    parent: str | None = Field(
        default=None, description="An existing node id, or null for a root."
    )
    status: str
    why: str = ""


class _Proposal(BaseModel):
    changes: list[_Change] = Field(
        default_factory=list, description="Changes to existing nodes."
    )
    new_nodes: list[_NewNode] = Field(
        default_factory=list,
        description=(
            "Only when the text clearly describes work that has no node yet. "
            "Prefer leaving it in `unmatched` if you are unsure."
        ),
    )
    unmatched: list[str] = Field(
        default_factory=list,
        description=(
            "Statements you could not confidently map onto any node. Put anything "
            "ambiguous here rather than guessing."
        ),
    )


_SYSTEM = """\
You map short status updates onto nodes in a project graph.

The graph has two kinds of node. **Work** nodes are things that can be
finished; their status is one of: {work_statuses}. **Contract** nodes are
agreements between subsystems (a schema, a handoff format); their status is one
of: {contract_statuses}, and they also carry an integer `version`.

Your only job is to decide which nodes the update refers to and what their
declared fields should become.

Rules:
- Change only what the update actually states. If someone says they finished
  one stage, do not infer anything about the stages around it.
- "We agreed on X" / "X is signed off" means a contract's status becomes
  `agreed`. Finishing the work that implements a contract is a change to the
  work node, not to the contract.
- Never mark a node done because its dependencies are done, and never mark a
  node blocked or ready. Readiness is computed from the graph, not declared.
- Copy node ids exactly as they appear in the inventory. Never invent one.
- If a statement does not clearly match a node, put it in `unmatched`. An
  honest `unmatched` entry is far more useful than a confident wrong node.
- Propose a new node only when the update plainly describes work that has no
  node at all. Prefer `unmatched`.
- Do not reason about consequences — what this unblocks is computed separately.

Graph inventory:

{inventory}
"""


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2}


def select_context(graph: Graph, text: str, limit: int = CONTEXT_LIMIT) -> list[str]:
    """Which node ids to describe to the model.

    Sending the whole graph is right until it isn't. Past the limit, score by
    word overlap with the update, but always keep contracts (there are few and
    they are what subsystems disagree about) and any node named outright.
    """
    ids = graph.ids()
    if len(ids) <= limit:
        return ids

    words = _tokens(text)
    lowered = text.lower()
    scored: list[tuple[int, str]] = []
    forced: list[str] = []
    for node_id in ids:
        node = graph.get(node_id)
        if node.kind == "contract" or node_id.lower() in lowered:
            forced.append(node_id)
            continue
        overlap = _tokens(
            node_id.replace(".", " ").replace("_", " ") + " " + node.title
        )
        scored.append((len(words & overlap), node_id))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    room = max(0, limit - len(forced))
    chosen = forced + [node_id for _score, node_id in scored[:room]]
    return sorted(set(chosen))


def build_inventory(graph: Graph, engine: Engine, node_ids: list[str]) -> str:
    """A compact, stable rendering of the graph for the prompt."""
    lines: list[str] = []
    for node_id in node_ids:
        node = graph.get(node_id)
        derived = engine.derived(node_id)
        head = (
            f"{node_id}  [{node.kind}]  status={node.status}  "
            f"readiness={derived.readiness}"
        )
        if node.version is not None:
            head += f"  version={node.version}"
        lines.append(f"{head}  {node.title!r}")
        if node.parent:
            lines.append(f"    parent: {node.parent}")
        for gate_name, expr in node.gates:
            lines.append(f"    gate {gate_name}: {expr}")
        if node.satisfied_by:
            lines.append(f"    implemented by: {', '.join(node.satisfied_by)}")
    return "\n".join(lines)


def _cache_key(model: str, inventory: str, text: str) -> str:
    blob = json.dumps(
        {"v": PROMPT_VERSION, "model": model, "inventory": inventory, "text": text},
        sort_keys=True,
    )
    return "propose:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


def _client():
    try:
        import anthropic
    except ImportError:  # pragma: no cover - depends on install extras
        raise ProposalError(
            "the `anthropic` package is required for `trellis log`; "
            "install it with: pip install 'trellis[llm]'"
        ) from None

    client = anthropic.Anthropic()
    # The SDK does not fail construction on missing credentials — it raises deep
    # inside the first request. Check up front so the error is actionable.
    if client.api_key is None and client.auth_token is None:
        raise MissingCredentialsError(
            "no Anthropic credentials configured; set ANTHROPIC_API_KEY "
            "(or run `ant auth login`)"
        )
    return client


def propose(
    graph: Graph,
    engine: Engine,
    text: str,
    cache: Cache | None = None,
    model: str = MODEL,
) -> Delta:
    """Map `text` onto a proposed Delta. Never writes; may hit the cache.

    The cache key covers the prompt version, the model, the inventory, and the
    text — so the same sentence against an unchanged graph costs nothing the
    second time, and any change to the graph or the prompt invalidates it.
    """
    node_ids = select_context(graph, text)
    inventory = build_inventory(graph, engine, node_ids)
    key = _cache_key(model, inventory, text)

    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return Delta.from_dict(hit)

    system = _SYSTEM.format(
        work_statuses=", ".join(WORK_STATUSES),
        contract_statuses=", ".join(CONTRACT_STATUSES),
        inventory=inventory,
    )

    client = _client()
    import anthropic

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            # The inventory dominates the prompt and is identical across repeated
            # updates, so it is worth a cache breakpoint on any real-sized graph.
            # Below ~1024 tokens the API simply will not cache it, which is fine.
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": f"Update:\n{text}"}],
            output_format=_Proposal,
        )
    except anthropic.AnthropicError as exc:
        raise ProposalError(f"model call failed: {exc}") from exc

    parsed = response.parsed_output
    if parsed is None:
        raise ProposalError(
            f"model did not return a usable proposal (stop_reason={response.stop_reason})"
        )

    delta = Delta(
        changes=[
            ProposedChange(c.node, c.field, c.value, c.why, c.confidence)
            for c in parsed.changes
        ],
        new_nodes=[
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "parent": n.parent,
                "status": n.status,
            }
            for n in parsed.new_nodes
        ],
        unmatched=list(parsed.unmatched),
        source=text,
    )
    delta = drop_noops(normalize(delta), graph)

    if cache is not None:
        cache.put(key, delta.as_dict())
    return delta
