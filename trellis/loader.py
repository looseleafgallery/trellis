"""Reading a graph off disk.

One YAML file per node keeps diffs literal: a "small change" to the system is
a small change to one file. Files may also hold a `nodes:` list when a set of
nodes is genuinely one editing unit (a stage and its gates, say).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .model import RESERVED_EXPORTS, Graph, ModelError, Node, node_from_dict

GRAPH_DIRNAME = "graph"


def project_root(graph_dir: str | os.PathLike) -> Path:
    """The directory that owns a graph's durable state.

    Resolved, always. Deriving it from the argument as spelled makes `.` and
    `graph` two different projects — so the same graph gets two journals, two
    caches, and two drift baselines, and half your history is invisible
    depending on where you were standing. State belongs to the graph, not to
    the invocation.
    """
    return Path(graph_dir).resolve().parent


def find_graph_dir(start: str | os.PathLike | None = None) -> Path:
    """Walk upward looking for a `graph/` directory, git-style."""
    cur = Path(start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        graph_dir = candidate / GRAPH_DIRNAME
        if graph_dir.is_dir():
            return graph_dir
    raise FileNotFoundError(
        f"no {GRAPH_DIRNAME}/ directory found in {cur} or any parent"
    )


def _node_dicts(payload, source: str) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "nodes" in payload:
            nodes = payload["nodes"]
            if not isinstance(nodes, list):
                raise ModelError(f"{source}: `nodes` must be a list")
            return nodes
        return [payload]
    raise ModelError(f"{source}: expected a mapping or list at the top level")


def _declared_mappings(document: yaml.Node | None) -> list[yaml.MappingNode]:
    """The mappings in a parse tree that `_node_dicts` will read as nodes.

    Mirrors that function's shape rules on the tree `compose` returns, where
    flow style and line numbers still exist - `safe_load` has discarded both by
    the time a dict reaches `node_from_dict`. A shape this does not recognise
    is skipped rather than refused: the pass below reports flow style and
    nothing else, and `_node_dicts` reads the same bytes and owns every other
    complaint about them.
    """
    if isinstance(document, yaml.SequenceNode):
        return [item for item in document.value if isinstance(item, yaml.MappingNode)]
    if isinstance(document, yaml.MappingNode):
        for key, value in document.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "nodes":
                if not isinstance(value, yaml.SequenceNode):
                    return []
                return [i for i in value.value if isinstance(i, yaml.MappingNode)]
        return [document]
    return []


def _reject_flow_style(document: yaml.Node | None, source: str) -> None:
    """Refuse `{id: a, status: done}` here, rather than at the first write.

    Such a node loads, validates and evaluates correctly, and no write against
    it can ever land. The failure is safe - nothing is written - but it arrives
    at `set` or `accept`, after the person has already decided, and flow style
    is what anything dumping a dict emits by default. So a graph that cannot be
    edited is refused where it is being read, with the line to change.

    Only the node's own mapping is judged. A flow value inside a block node -
    `evidence: {how: verified, at: ...}` - has a line the writer can find, and
    the shipped example graph uses one.
    """
    for mapping in _declared_mappings(document):
        if not mapping.flow_style:
            continue
        declared = next(
            (
                value.value
                for key, value in mapping.value
                if isinstance(key, yaml.ScalarNode)
                and key.value == "id"
                and isinstance(value, yaml.ScalarNode)
            ),
            "",
        )
        what = f"node {declared!r}" if declared else "a node"
        raise ModelError(
            f"{source}:{mapping.start_mark.line + 1}: {what} is written in YAML "
            f"flow style, which trellis can read but can never write to - `set` "
            f"and `accept` rewrite one field's line, and a node inside `{{...}}` "
            f"has no line of its own. Write it as a block mapping, one field "
            f"per line."
        )


def load_graph(graph_dir: str | os.PathLike) -> Graph:
    graph_dir = Path(graph_dir)
    nodes: dict[str, Node] = {}

    paths = sorted(
        p for p in graph_dir.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file()
    )
    for path in paths:
        rel = str(path.relative_to(graph_dir))
        text = path.read_text()
        try:
            # Parsed twice deliberately: flow style and line numbers live on the
            # parse tree, and the loaded dicts remember neither.
            document = yaml.compose(text, Loader=yaml.SafeLoader)
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ModelError(f"{rel}: invalid YAML: {exc}") from exc
        _reject_flow_style(document, rel)
        for data in _node_dicts(payload, rel):
            node = node_from_dict(data, source=rel)
            if node.id in nodes:
                raise ModelError(
                    f"{rel}: duplicate node id {node.id!r} "
                    f"(already declared in {nodes[node.id].source})"
                )
            nodes[node.id] = node

    for node in nodes.values():
        last = node.id.rsplit(".", 1)[-1]
        if last in RESERVED_EXPORTS:
            raise ModelError(
                f"{node.source}: node id {node.id!r} ends in the reserved export "
                f"name {last!r}, which would make references to it ambiguous"
            )

    return Graph(nodes)
