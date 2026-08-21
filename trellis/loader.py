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


def load_graph(graph_dir: str | os.PathLike) -> Graph:
    graph_dir = Path(graph_dir)
    nodes: dict[str, Node] = {}

    paths = sorted(
        p for p in graph_dir.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file()
    )
    for path in paths:
        rel = str(path.relative_to(graph_dir))
        try:
            payload = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ModelError(f"{rel}: invalid YAML: {exc}") from exc
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
