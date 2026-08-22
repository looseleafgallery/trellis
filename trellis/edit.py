"""Writing declared changes back to YAML.

These files are written and read by people: they carry comments, `notes: >`
blocks, and a deliberate ordering. A parse-and-dump round trip would silently
reformat all of that, so this module edits the specific line instead and leaves
every other byte alone.

Line surgery is only safe with a check behind it. Every write is followed by a
reload that verifies the intended change landed *and* that no other node's
fingerprint moved; if either fails, the original bytes are restored and the
write is reported as failed. That check is what makes the approach acceptable —
without it, this would be a text-munging bug waiting to happen.

Only scalar fields are writable (see delta.EDITABLE_FIELDS). Rewriting a
`gates:` block is a structural edit, and structural edits belong in your editor
where you can see what you are doing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .delta import EDITABLE_FIELDS, Delta
from .loader import load_graph
from .model import Graph, Node


class EditError(RuntimeError):
    """A write could not be made safely. Nothing was changed."""


@dataclass
class WriteResult:
    node: str
    field: str
    before: object
    after: object
    path: str
    created: bool = False

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "path": self.path,
            "created": self.created,
        }


_SAFE_SCALAR = re.compile(r"[A-Za-z_][A-Za-z0-9_./\-]*")


def _fmt(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if _SAFE_SCALAR.fullmatch(text):
        return text
    # JSON string syntax is a valid YAML double-quoted scalar.
    return json.dumps(text)


def _split_comment(rest: str) -> tuple[str, str]:
    """Separate a scalar's value from any trailing comment.

    A `#` only starts a comment outside quotes and after whitespace. Treating
    every `#` as a comment turned `ref: "#20"` into `ref: TRE-5  #20"` on
    rewrite — which still *parsed* as `TRE-5`, so the verify step passed while
    the file gained garbage. Correct semantics, corrupt bytes.
    """
    text = rest.lstrip()
    offset = len(rest) - len(text)
    if text[:1] in ('"', "'"):
        quote = text[0]
        index = 1
        while index < len(text):
            if text[index] == "\\" and quote == '"':
                index += 2
                continue
            if text[index] == quote:
                index += 1
                break
            index += 1
        after = text[index:]
        hash_at = after.find("#")
        if hash_at == -1:
            return rest, ""
        return rest[: offset + index + hash_at], after[hash_at:].strip()

    for index, char in enumerate(text):
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return rest[: offset + index], text[index:].strip()
    return rest, ""


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_block(lines: list[str], node_id: str) -> tuple[int, int, int]:
    """Locate a node's block. Returns (start, end, key_indent)."""
    id_pattern = re.compile(
        r'^(\s*)(-\s+)?id:\s*["\']?' + re.escape(node_id) + r'["\']?\s*$'
    )
    index = next((i for i, line in enumerate(lines) if id_pattern.match(line)), None)
    if index is None:
        raise EditError(f"could not locate node {node_id!r} in its source file")

    match = id_pattern.match(lines[index])
    lead, dash = match.group(1), match.group(2)
    key_indent = len(lead) + (len(dash) if dash else 0)

    if dash:
        start = index
        item_indent = len(lead)
    else:
        start = index
        item_indent = None
        for j in range(index - 1, -1, -1):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if lines[j].lstrip().startswith("- ") and _indent_of(lines[j]) < key_indent:
                start, item_indent = j, _indent_of(lines[j])
                break
            if _indent_of(lines[j]) < key_indent:
                break

    end = len(lines)
    for j in range(index + 1, len(lines)):
        line = lines[j]
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = _indent_of(line)
        if indent < key_indent:
            end = j
            break
        if (
            item_indent is not None
            and indent == item_indent
            and line.lstrip().startswith("- ")
        ):
            end = j
            break
    return start, end, key_indent


def _set_field(lines: list[str], node_id: str, field: str, value: object) -> list[str]:
    start, end, key_indent = _find_block(lines, node_id)
    field_pattern = re.compile(r"^(\s*(?:-\s+)?)" + re.escape(field) + r":(.*)$")

    for i in range(start, end):
        match = field_pattern.match(lines[i])
        if not match or len(match.group(1)) != key_indent:
            continue
        rest = match.group(2)
        if rest.strip() in ("|", ">", "|-", ">-", "") and field != "parent":
            raise EditError(f"{node_id}: {field!r} is a block scalar; edit it by hand")
        _value, comment = _split_comment(rest)
        trailing = f"  {comment}" if comment else ""
        lines[i] = f"{match.group(1)}{field}: {_fmt(value)}{trailing}"
        return lines

    # Field absent — insert it directly after the id line.
    id_pattern = re.compile(
        r'^(\s*)(-\s+)?id:\s*["\']?' + re.escape(node_id) + r'["\']?\s*$'
    )
    for i in range(start, end):
        if id_pattern.match(lines[i]):
            lines.insert(i + 1, f"{' ' * key_indent}{field}: {_fmt(value)}")
            return lines
    raise EditError(f"{node_id}: could not place field {field!r}")


def _render_new_node(spec: dict) -> str:
    order = [
        "id",
        "kind",
        "title",
        "parent",
        "status",
        "version",
        "provides",
        "satisfied_by",
        "gates",
        "notes",
    ]
    lines: list[str] = []
    for key in order:
        if key not in spec or spec[key] in (None, [], {}, ""):
            continue
        value = spec[key]
        if key == "gates" and isinstance(value, dict):
            lines.append("gates:")
            for gate_name, gate_expr in value.items():
                lines.append(f"  {gate_name}: {_fmt(gate_expr)}")
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_fmt(v) for v in value)}]")
        else:
            lines.append(f"{key}: {_fmt(value)}")
    return "\n".join(lines) + "\n"


def _new_node_path(graph_dir: Path, node_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", node_id)
    path = graph_dir / f"{safe}.yaml"
    counter = 2
    while path.exists():
        path = graph_dir / f"{safe}-{counter}.yaml"
        counter += 1
    return path


def _append_list_field(
    lines: list[str], node_id: str, field: str, value: str
) -> list[str]:
    """Add one entry to a flow-style list field, creating it if absent.

    Only flow style (`acknowledge: [a, b]`) is written or rewritten. A block
    list is refused rather than guessed at — appending to one means deciding
    where the block ends, and a wrong guess corrupts the node quietly.
    """
    start, end, key_indent = _find_block(lines, node_id)
    pattern = re.compile(r"^(\s*(?:-\s+)?)" + re.escape(field) + r":(.*)$")

    for i in range(start, end):
        match = pattern.match(lines[i])
        if not match or len(match.group(1)) != key_indent:
            continue
        rest = match.group(2).strip()
        if not rest.startswith("[") or not rest.endswith("]"):
            raise EditError(
                f"{node_id}: {field!r} is not a single-line list; add {value!r} by hand"
            )
        existing = [v.strip() for v in rest[1:-1].split(",") if v.strip()]
        if value in existing:
            return lines
        existing.append(value)
        lines[i] = f"{match.group(1)}{field}: [{', '.join(existing)}]"
        return lines

    id_pattern = re.compile(
        r'^(\s*)(-\s+)?id:\s*["\']?' + re.escape(node_id) + r'["\']?\s*$'
    )
    for i in range(start, end):
        if id_pattern.match(lines[i]):
            lines.insert(i + 1, f"{' ' * key_indent}{field}: [{value}]")
            return lines
    raise EditError(f"{node_id}: could not place field {field!r}")


def acknowledge(
    graph_dir: str | Path, graph: Graph, node_id: str, code: str
) -> WriteResult:
    """Record on the node that this finding has been answered for good.

    Verified the same way a status write is: reload, confirm the acknowledgement
    took, and confirm nothing else moved. Restores the original bytes otherwise.
    """
    graph_dir = Path(graph_dir)
    node = graph.get(node_id)
    path = graph_dir / node.source
    original = path.read_bytes()

    try:
        lines = _append_list_field(
            path.read_text().splitlines(), node_id, "acknowledge", code
        )
        path.write_text("\n".join(lines) + "\n")

        after = load_graph(graph_dir)
        if not after.get(node_id).acknowledges(code):
            raise EditError(f"{node_id}: acknowledgement of {code!r} did not take")
        for other_id, other in graph.nodes.items():
            if other_id == node_id:
                continue
            if (
                other_id not in after
                or after.get(other_id).fingerprint() != other.fingerprint()
            ):
                raise EditError(f"{other_id}: changed as a side effect of the write")
    except Exception:
        path.write_bytes(original)
        raise

    return WriteResult(
        node=node_id,
        field="acknowledge",
        before=list(node.acknowledge),
        after=[*node.acknowledge, code],
        path=str(path),
    )


def node_line(graph_dir: str | Path, graph: Graph, node_id: str) -> tuple[Path, int]:
    """Where this node is declared, so an editor can open at it."""
    node = graph.get(node_id)
    path = Path(graph_dir) / node.source
    pattern = re.compile(
        r'^(\s*)(-\s+)?id:\s*["\']?' + re.escape(node_id) + r'["\']?\s*$'
    )
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if pattern.match(line):
            return path, number
    return path, 1


def apply_delta(graph_dir: str | Path, graph: Graph, delta: Delta) -> list[WriteResult]:
    """Write a validated delta to disk, or change nothing at all.

    New nodes are written to their own file rather than appended into an
    existing one: appending means guessing at the surrounding document's shape,
    and a fresh file is unambiguous. Move it wherever you like afterwards.
    """
    graph_dir = Path(graph_dir)
    for change in delta.changes:
        if change.field not in EDITABLE_FIELDS:
            raise EditError(f"{change.node}: {change.field!r} is not writable")

    by_file: dict[Path, list] = {}
    for change in delta.changes:
        if change.node not in graph:
            continue  # a field on a node this same delta is creating
        node = graph.get(change.node)
        by_file.setdefault(graph_dir / node.source, []).append(change)

    created_specs = {spec["id"]: spec for spec in delta.new_nodes}
    created_paths = {
        node_id: _new_node_path(graph_dir, node_id) for node_id in created_specs
    }
    # Fold changes that target a node this delta creates into its spec.
    for change in delta.changes:
        if change.node in created_specs:
            created_specs[change.node][change.field] = change.value

    snapshot = {path: path.read_bytes() for path in by_file}
    results: list[WriteResult] = []

    try:
        for path, changes in by_file.items():
            lines = path.read_text().splitlines()
            for change in changes:
                before = getattr(graph.get(change.node), change.field, None)
                lines = _set_field(lines, change.node, change.field, change.value)
                results.append(
                    WriteResult(
                        change.node, change.field, before, change.value, str(path)
                    )
                )
            path.write_text("\n".join(lines) + "\n")

        for node_id, spec in created_specs.items():
            path = created_paths[node_id]
            path.write_text(_render_new_node(spec))
            results.append(
                WriteResult(
                    node_id, "*", None, spec.get("status"), str(path), created=True
                )
            )

        _verify(graph_dir, graph, delta, created_specs)
    except Exception:
        for path, data in snapshot.items():
            path.write_bytes(data)
        for path in created_paths.values():
            path.unlink(missing_ok=True)
        raise

    return results


def _verify(
    graph_dir: Path, before: Graph, delta: Delta, created: dict[str, dict]
) -> None:
    """Reload from disk and prove the write did exactly what was asked."""
    after = load_graph(graph_dir)

    for change in delta.changes:
        if change.node not in after:
            raise EditError(f"{change.node}: vanished from the graph after writing")
        actual = getattr(after.get(change.node), change.field, None)
        if actual != change.value:
            raise EditError(
                f"{change.node}.{change.field}: wrote {change.value!r} but the file "
                f"reloaded as {actual!r}"
            )

    for node_id in created:
        if node_id not in after:
            raise EditError(f"{node_id}: new node did not load back")

    touched = {c.node for c in delta.changes} | set(created)
    for node_id, node in before.nodes.items():
        if node_id in touched:
            continue
        if node_id not in after:
            raise EditError(f"{node_id}: disappeared as a side effect of the write")
        if after.get(node_id).fingerprint() != node.fingerprint():
            raise EditError(f"{node_id}: changed as a side effect of the write")


def unchanged(node: Node, field: str, value: object) -> bool:
    return getattr(node, field, None) == value
