"""The requirement expression language.

Gates are written as small boolean expressions over other nodes' exports:

    agent.plan.done and contract.tool_schema.live and contract.tool_schema.version >= 2

The grammar is a whitelisted subset of Python's, parsed with `ast` and walked
by hand — real operator syntax, no arbitrary code execution.

Two properties matter more than expressiveness:

1. **References are extractable.** The node ids appearing in an expression are
   the dependency edges. There is no separate `depends_on` list to drift.
2. **Evaluation is traced.** Every sub-expression's value is recorded, so a
   failing gate can report exactly which conjuncts were unmet instead of a
   bare `False`. That trace is what makes `explain` possible without an LLM.

Evaluation deliberately does *not* short-circuit: `a and b` evaluates both
sides so an explanation lists every unmet reason, not just the first.
"""

from __future__ import annotations

import ast
import functools
import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ExprError(ValueError):
    """The expression is malformed, or references something unresolvable."""


@dataclass
class Trace:
    """One node of an evaluation tree."""

    src: str
    value: Any
    op: str
    children: tuple[Trace, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.value)

    def unmet(self) -> list[Trace]:
        """The smallest set of sub-expressions responsible for falsity.

        An `and` decomposes into its failing conjuncts. An `or` does not — if
        every branch failed, the useful statement is "none of these held", so
        the disjunction is reported whole.
        """
        if self.ok:
            return []
        if self.op == "and":
            out: list[Trace] = []
            for child in self.children:
                out.extend(child.unmet())
            return out or [self]
        return [self]

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "value": self.value,
            "op": self.op,
            "children": [c.to_dict() for c in self.children],
        }


_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _exports_done(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("done"))
    return bool(value)


def _all_done(*nodes: Any) -> bool:
    return all(_exports_done(n) for n in nodes)


def _any_done(*nodes: Any) -> bool:
    return any(_exports_done(n) for n in nodes)


def _count_done(*nodes: Any) -> int:
    return sum(1 for n in nodes if _exports_done(n))


def _at_least(n: Any, *conds: Any) -> bool:
    if not isinstance(n, int):
        raise ExprError("at_least() takes a count as its first argument")
    return sum(1 for c in conds if _exports_done(c)) >= n


def _has(node: Any, tag: Any) -> bool:
    if not isinstance(node, dict):
        raise ExprError("has() takes a node reference as its first argument")
    return tag in (node.get("provides") or ())


BUILTINS: dict[str, Callable[..., Any]] = {
    "all_done": _all_done,
    "any_done": _any_done,
    "count_done": _count_done,
    "at_least": _at_least,
    "has": _has,
    "len": len,
}


@functools.lru_cache(maxsize=512)
def parse(src: str) -> ast.Expression:
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"cannot parse {src!r}: {exc.msg}") from exc
    return tree


def _dotted(node: ast.AST) -> str | None:
    """Flatten an attribute chain (`a.b.c`) back into a dotted string."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def references(src: str) -> set[str]:
    """Every dotted reference in the expression, ignoring builtin call names."""
    tree = parse(src)
    found: set[str] = set()

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            # The callee is a builtin name, not a node reference.
            for arg in node.args:
                walk(arg)
            return
        if isinstance(node, (ast.Attribute, ast.Name)):
            dotted = _dotted(node)
            if dotted and dotted.split(".")[0] not in BUILTINS:
                found.add(dotted)
            return
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree.body)
    return found


# Builtins whose arguments are a list of node references, and which still mean
# something with one fewer of them. `has` and `len` take a single subject, so
# dropping its reference would leave a call about nothing — those go whole.
_OVER_NODES = {"all_done", "any_done", "count_done"}
# The same, except the first argument is the threshold rather than a node.
_OVER_NODES_AFTER_COUNT = {"at_least"}


def _names(node: ast.AST, drop: Callable[[str], bool]) -> bool:
    """Whether this sub-expression mentions a reference `drop` selects.

    Asked through `references` rather than by walking here a second time, so
    what counts as a reference is decided in exactly one place — the same
    place that decides where the edges are.
    """
    return any(drop(dotted) for dotted in references(ast.unparse(node)))


def _pruned(node: ast.AST, drop: Callable[[str], bool]) -> ast.AST | None:
    """The sub-expression without the selected references, or `None` if nothing
    of it survives."""
    if not _names(node, drop):
        return node

    if isinstance(node, ast.BoolOp):
        kept = [p for p in (_pruned(v, drop) for v in node.values) if p is not None]
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        return ast.BoolOp(op=node.op, values=kept)

    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name in _OVER_NODES_AFTER_COUNT:
            head, rest = node.args[:1], node.args[1:]
        elif name in _OVER_NODES:
            head, rest = [], node.args
        else:
            return None
        kept = [p for p in (_pruned(a, drop) for a in rest) if p is not None]
        # `at_least(2)` is not a weaker `at_least(2, a.done)`, it is a gate
        # that can never open. With no subjects left the call goes whole.
        if not kept:
            return None
        return ast.Call(func=node.func, args=[*head, *kept], keywords=[])

    return None


def without_references(src: str, drop: Callable[[str], bool]) -> str | None:
    """`src` as it would read had it never named the references `drop` selects.

    What is removed is the smallest *boolean term* containing the reference,
    not the reference itself. `a.version >= 2` says nothing once `a` is gone,
    and putting a literal where `a.version` was would quietly change what the
    comparison asks rather than remove the requirement. So a conjunct goes, a
    disjunct goes, an argument to `all_done` goes, and anything else holding
    the reference goes whole.

    `None` means nothing survived, which is the honest answer for a gate whose
    only requirement was that reference: the gate goes with it.

    Coarse in one direction, deliberately. A term naming two nodes takes both
    with it, so a caller measuring one edge should compare the references
    before and after and say what else it had to drop.
    """
    body = parse(src).body
    kept = _pruned(body, drop)
    if kept is None:
        return None
    if kept is body:
        return src
    return ast.unparse(ast.Expression(body=kept))


class _Evaluator:
    def __init__(self, src: str, resolve: Callable[[str], Any]):
        self.src = src
        self.resolve = resolve

    def segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.src, node) or self.src

    def eval(self, node: ast.AST) -> Trace:
        if isinstance(node, ast.BoolOp):
            op = "and" if isinstance(node.op, ast.And) else "or"
            # No short-circuit: every branch is evaluated so the trace is complete.
            children = tuple(self.eval(v) for v in node.values)
            values = [c.ok for c in children]
            value = all(values) if op == "and" else any(values)
            return Trace(self.segment(node), value, op, children)

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            child = self.eval(node.operand)
            return Trace(self.segment(node), not child.ok, "not", (child,))

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            child = self.eval(node.operand)
            return Trace(self.segment(node), -child.value, "neg", (child,))

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise ExprError("chained comparisons are not supported")
            left = self.eval(node.left)
            right = self.eval(node.comparators[0])
            fn = _COMPARE.get(type(node.ops[0]))
            if fn is None:
                raise ExprError(f"comparison {type(node.ops[0]).__name__} not allowed")
            try:
                value = fn(left.value, right.value)
            except TypeError as exc:
                raise ExprError(f"{self.segment(node)}: {exc}") from exc
            return Trace(self.segment(node), value, "cmp", (left, right))

        if isinstance(node, ast.Constant):
            return Trace(self.segment(node), node.value, "const")

        if isinstance(node, (ast.List, ast.Tuple)):
            children = tuple(self.eval(e) for e in node.elts)
            return Trace(
                self.segment(node), [c.value for c in children], "list", children
            )

        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in BUILTINS:
                raise ExprError(f"unknown function {name or '<expr>'!r}")
            if node.keywords:
                raise ExprError(f"{name}() takes no keyword arguments")
            children = tuple(self.eval(a) for a in node.args)
            value = BUILTINS[name](*[c.value for c in children])
            return Trace(self.segment(node), value, "call", children)

        if isinstance(node, (ast.Attribute, ast.Name)):
            dotted = _dotted(node)
            if dotted is None:
                raise ExprError(f"unsupported reference: {self.segment(node)}")
            if dotted == "true" or dotted == "false":
                raise ExprError("use True/False, not true/false")
            return Trace(dotted, self.resolve(dotted), "ref")

        raise ExprError(f"unsupported syntax: {type(node).__name__}")


def evaluate(src: str, resolve: Callable[[str], Any]) -> Trace:
    """Evaluate `src`, returning the full trace (its `.value` is the result)."""
    return _Evaluator(src, resolve).eval(parse(src).body)
