"""The `trellis` command line."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import delta as delta_mod
from . import edit, journal, queries, viz
from . import evidence as evidence_mod
from . import snapshot as snapshot_mod
from .cache import Cache
from .engine import CycleError, Derived, Engine
from .loader import find_graph_dir, load_graph, project_root
from .model import Graph, ModelError, is_retreat

MARKS = {
    "blocked": "x",
    "ready": ">",
    "active": "~",
    "unverified": "*",
    "awaiting": "?",
    "done": "+",
    "superseded": "-",
    "abandoned": "-",
    "live": "+",
    "pending": "~",
    "unagreed": ".",
}


def _cache_path(graph_dir: Path, enabled: bool) -> Path | None:
    return project_root(graph_dir) / ".trellis" / "cache.json" if enabled else None


def _load(args) -> tuple[Graph, Cache, Path]:
    graph_dir = Path(args.graph) if args.graph else find_graph_dir()
    graph = load_graph(graph_dir)
    cache = Cache.load(_cache_path(graph_dir, not args.no_cache))
    return graph, cache, graph_dir


def _emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))


def _node_json(graph: Graph, d: Derived) -> dict:
    """A derived record, plus the external id the node declares.

    `ref` is read from the graph rather than carried on `Derived`, which is
    persisted: it is declared state, and it is deliberately outside the
    fingerprint, so a cached record would have no reason to be invalidated when
    it changed. Merging it at emit time keeps the cache honest and the join
    current.
    """
    payload = d.as_dict()
    payload["ref"] = graph.get(d.id).ref or None
    return payload


def _resolve(graph: Graph, token: str) -> str | None:
    """A node id, or an external `ref:` standing in for one.

    Node ids always win. A ref is only consulted when the token names no node,
    so adding a `ref:` can never change what an existing command means — and a
    ref matching several nodes resolves to nothing rather than to a guess,
    because picking one would be inventing an answer the graph does not have.

    Returns None when nothing matched; the caller reports it.
    """
    if token in graph:
        return token
    matches = graph.by_ref(token)
    return matches[0] if len(matches) == 1 else None


def _unknown(graph: Graph, token: str) -> int:
    """Report a token that named neither a node nor exactly one ref."""
    matches = graph.by_ref(token)
    if len(matches) > 1:
        print(
            f"error: ref {token!r} is declared by {len(matches)} nodes "
            f"({', '.join(matches)}) - name one of them",
            file=sys.stderr,
        )
    else:
        print(f"error: unknown node {token!r}", file=sys.stderr)
    return 2


# -- commands ---------------------------------------------------------------


def cmd_check(args) -> int:
    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    problems, muted = queries.check_with_muted(graph, engine, graph_dir)
    cache.save()

    if args.json:
        _emit([p.as_dict() for p in problems], True)
    else:
        if not problems:
            tail = f" ({muted} acknowledged)" if muted else ""
            print(f"ok - {len(graph)} nodes, no problems{tail}")
        else:
            # Already ranked by check(): worst first, then most urgent.
            for p in problems:
                print(f"{p.severity:5} {p.node}: {p.message} [{p.code}]")
            errors = sum(1 for p in problems if p.severity == "error")
            print(f"\n{len(problems)} problem(s), {errors} error(s)")
            if muted:
                print(f"{muted} acknowledged and not shown")
            first = problems[0]
            print(f"\nstart with: {first.node} - {first.code}")
            remedy = REMEDIES.get(first.code)
            if remedy:
                print(f"  -> {remedy}")
    return 1 if any(p.severity == "error" for p in problems) else 0


def _print_tree(
    engine: Engine,
    derived: dict[str, Derived],
    roots: list[str],
    show_ref: bool = False,
) -> None:
    def walk(node_id: str, depth: int) -> None:
        d = derived[node_id]
        node = engine.graph.get(node_id)
        mark = MARKS.get(d.readiness, "?")
        indent = "  " * depth
        label = node.title if node.title != node_id else node_id
        extra = ""
        if engine.graph.children_of(node_id):
            extra = (
                f"  [{d.exports.get('leaf_done', 0)}/{d.exports.get('leaf_total', 0)}]"
            )
        elif d.kind == "contract" and node.version is not None:
            extra = f"  [v{node.version}]"
        ref = f"  ({node.ref})" if show_ref and node.ref else ""
        flags = ""
        if any(v["severity"] == "error" for v in d.violations):
            flags = "  !"
        elif d.violations:
            flags = "  ?"
        width = max(12, 34 - len(indent))
        print(
            f"{indent}{mark} {node_id:<{width}} {d.readiness:<9} "
            f"{label}{extra}{ref}{flags}"
        )
        for child in engine.graph.children_of(node_id):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)


def cmd_state(args) -> int:
    graph, cache, _ = _load(args)
    engine = Engine(graph, cache)
    derived = engine.all_derived()
    cache.save()

    if args.node:
        requested = args.node
        args.node = _resolve(graph, requested)
        if args.node is None:
            return _unknown(graph, requested)
        d = derived[args.node]
        if args.json:
            _emit(_node_json(graph, d), True)
            return 0
        node = graph.get(args.node)
        print(f"{node.id}  ({node.kind})")
        if node.title != node.id:
            print(f"  title      {node.title}")
        if node.ref:
            print(f"  ref        {node.ref}")
        print(f"  status     {node.status}")
        print(f"  readiness  {d.readiness}")
        for name, gate in sorted(d.gates.items()):
            state = "satisfied" if gate.satisfied else "UNMET"
            print(f"  gate:{name:<6} {state}  {gate.expr}")
            for unmet in gate.unmet:
                print(f"    unmet    {unmet['src']}")
            if gate.error:
                print(f"    error    {gate.error}")
        if node.provides:
            print(f"  provides   {', '.join(node.provides)}")
        deps = graph.dependencies_of(node.id)
        if deps:
            print(f"  depends on {', '.join(deps)}")
        dependents = graph.dependents_of(node.id)
        if dependents:
            print(f"  feeds      {', '.join(dependents)}")
        print(f"  exports    {json.dumps(d.exports, default=str)}")
        for v in d.violations:
            print(f"  {v['severity']:5}      {v['message']}")
        return 0

    if args.json:
        _emit(
            {
                "summary": queries.summary(engine),
                "nodes": {k: _node_json(graph, v) for k, v in derived.items()},
            },
            True,
        )
        return 0

    roots = sorted(n.id for n in graph if not n.parent and n.kind == "work")
    contracts = sorted(n.id for n in graph if n.kind == "contract" and not n.parent)
    _print_tree(engine, derived, roots, args.ref)
    if contracts:
        print()
        _print_tree(engine, derived, contracts, args.ref)

    s = queries.summary(engine)
    counts = ", ".join(f"{k}: {v}" for k, v in sorted(s["counts"].items()))
    print(f"\n{counts}")
    for root, info in s["roots"].items():
        print(f"{root}: {info['leaf_done']}/{info['leaf_total']} leaves done")
    return 0


def cmd_ready(args) -> int:
    graph, cache, _ = _load(args)
    engine = Engine(graph, cache)
    items = queries.ready(engine, include_active=args.active)
    cache.save()
    if args.json:
        _emit([_node_json(graph, d) for d in items], True)
        return 0
    if not items:
        print("nothing is ready - run `trellis explain <node>` to see what is blocking")
        return 0
    for d in items:
        node = graph.get(d.id)
        kids = " (parent)" if graph.children_of(d.id) else ""
        print(f"{MARKS[d.readiness]} {d.id:<34} {node.title}{kids}")
    return 0


def _provenance(reason: queries.Reason) -> str:
    if not reason.how:
        return ""
    if reason.how in ("inferred", "assumed"):
        return f"   [edge {reason.how}, never confirmed - check this]"
    stamped = f" {reason.at}" if reason.at else ""
    return f"   [edge {reason.how}{stamped}]"


def _print_reasons(reasons: list[queries.Reason], depth: int = 0) -> None:
    for reason in reasons:
        indent = "  " * depth
        if reason.gate:
            print(f"{indent}- {reason.detail}")
        else:
            print(
                f"{indent}- {reason.src}  ->  {reason.node} is {reason.detail}"
                f"{_provenance(reason)}"
            )
        _print_reasons(reason.children, depth + 1)


def cmd_explain(args) -> int:
    graph, cache, _ = _load(args)
    engine = Engine(graph, cache)
    requested = args.node
    args.node = _resolve(graph, requested)
    if args.node is None:
        return _unknown(graph, requested)
    d = engine.derived(args.node)
    reasons = queries.explain(engine, args.node, args.gate)
    cache.save()

    if args.json:
        _emit(
            {
                "node": args.node,
                "readiness": d.readiness,
                "reasons": [r.as_dict() for r in reasons],
            },
            True,
        )
        return 0

    print(f"{args.node} is {d.readiness}")
    if not reasons:
        gate = d.gates.get(args.gate)
        if d.kind == "contract":
            print("  contract is live - it gates nothing right now")
        elif gate is None:
            print(f"  no {args.gate!r} gate declared - nothing gates this node")
        else:
            print(f"  gate {args.gate!r} is satisfied")
        return 0
    if args.gate in d.gates:
        print(f"  gate {args.gate!r}: {d.gates[args.gate].expr}")
    print()
    _print_reasons(reasons)
    roots = []
    for reason in reasons:
        roots.extend(reason.root_causes())
    unique = sorted({r.node for r in roots if r.node != args.node})
    if unique:
        print(f"\nroot cause(s): {', '.join(unique)}")
    return 0


def _parse_set(target: str, assignments: list[str]) -> dict[str, dict]:
    """`status=done` targets the named node; `other.node@status=done` targets another."""
    overlay: dict[str, dict] = {}
    for raw in assignments:
        node_id, sep, rest = raw.partition("@")
        if not sep:
            node_id, rest = target, raw
        field, sep, value = rest.partition("=")
        if not sep:
            raise SystemExit(f"error: --set expects field=value, got {raw!r}")
        field, value = field.strip(), value.strip()
        parsed: object = value
        if field == "version":
            parsed = int(value)
        elif field in ("provides", "satisfied_by"):
            parsed = [v.strip() for v in value.split(",") if v.strip()]
        overlay.setdefault(node_id, {})[field] = parsed
    return overlay


def cmd_impact(args) -> int:
    graph, cache, graph_dir = _load(args)
    requested = args.node
    args.node = _resolve(graph, requested)
    if args.node is None:
        return _unknown(graph, requested)
    overlay = _parse_set(args.node, args.set or ["status=done"])
    for node_id in overlay:
        if node_id not in graph:
            print(f"error: unknown node {node_id!r}", file=sys.stderr)
            return 2
    result = queries.impact(graph, overlay, cache)
    cache.save()
    evidence = evidence_mod.gather(graph_dir, graph)

    if args.json:
        _emit(result.as_dict(), True)
        return 0

    described = "; ".join(
        f"{n}: " + ", ".join(f"{k}={v}" for k, v in fields.items())
        for n, fields in overlay.items()
    )
    print(f"what if -> {described}\n")

    _print_impact(graph, result, args.verbose, evidence)
    return 0


def _title(graph: Graph, node_id: str) -> str:
    return graph.get(node_id).title if node_id in graph else "(new)"


def _churn_note(evidence: dict | None, node_id: str) -> str:
    if not evidence:
        return ""
    item = evidence.get(node_id)
    if not item or item.band != "churning" or not item.revisions:
        return ""
    shared = " (file shared)" if not item.precise else ""
    return f"   [declaration revised {item.revisions}x{shared} - check it is current]"


def _print_impact(
    graph: Graph,
    result: queries.Impact,
    verbose: bool = False,
    evidence: dict | None = None,
) -> None:
    if not result.changes:
        print("no effect anywhere in the graph")
    if result.created:
        print("creates:")
        for node_id in result.created:
            print(f"  + {node_id}")
    if result.unlocked:
        print("unlocks:")
        for node_id in result.unlocked:
            print(
                f"  > {node_id}  {_title(graph, node_id)}{_churn_note(evidence, node_id)}"
            )
    if result.contracts_lit:
        print("contracts that go live:")
        for node_id in result.contracts_lit:
            print(f"  + {node_id}{_churn_note(evidence, node_id)}")
    if result.contracts_dark:
        print("contracts that stop being live:")
        for node_id in result.contracts_dark:
            print(f"  - {node_id}")
    if result.newly_blocked:
        print("newly blocked:")
        for node_id in result.newly_blocked:
            print(f"  x {node_id}  {_title(graph, node_id)}")
    if result.violations_introduced:
        print("violations introduced:")
        for p in result.violations_introduced:
            print(f"  ! {p.node}: {p.message}")
    if result.violations_cleared:
        print("violations cleared:")
        for p in result.violations_cleared:
            print(f"  . {p.node}: {p.message}")

    skip = set(result.unlocked) | set(result.newly_blocked) | set(result.created)
    other = [c for c in result.changes if c.field_ != "status" and c.node not in skip]
    if other and verbose:
        print("other derived changes:")
        for c in other:
            print(f"  {c.node}  {c.field_}: {c.before} -> {c.after}")

    print(
        f"\ncost: recomputed {result.nodes_recomputed}/{result.nodes_total} nodes "
        f"({result.nodes_reused} reused from cache)"
    )


# -- the write loop ---------------------------------------------------------


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _print_delta(graph: Graph, delta: delta_mod.Delta) -> None:
    for spec in delta.new_nodes:
        kind = spec.get("kind", "work")
        print(f"  + create {spec['id']}  ({kind}, status={spec.get('status')})")
        if spec.get("why"):
            print(f"      ({spec['why']})")
    for change in delta.changes:
        if change.node in graph:
            before = getattr(graph.get(change.node), change.field, None)
            print(f"  ~ {change.node}  {change.field}: {before} -> {change.value}")
        else:
            print(f"  ~ {change.node}  {change.field} = {change.value}")
        detail = [d for d in (change.why,) if d]
        if change.confidence < 1.0:
            detail.append(f"confidence {change.confidence:.0%}")
        if detail:
            print(f"      ({'; '.join(detail)})")
    for item in delta.unmatched:
        print(f"  ? unmatched: {item}")


def _retreats(graph: Graph, delta) -> list[tuple[str, object, object]]:
    """Changes in this delta that walk a status backwards."""
    out = []
    for change in delta.changes:
        if change.field != "status" or change.node not in graph:
            continue
        before = graph.get(change.node).status
        if is_retreat(before, change.value):
            out.append((change.node, before, change.value))
    return out


def _ask_why(retreats: list[tuple[str, object, object]]) -> str | None:
    """Ask for a reason at the moment a belief is revised, not afterwards.

    A correction is the most informative thing that happens to a graph, and the
    why is the part that is not recoverable later. Asking here is the only
    moment the answer is cheap.
    """
    for node, before, after in retreats:
        print(f"  correction: {node} goes back from {before} to {after}")
    if not sys.stdin.isatty():
        return None
    try:
        answer = input("why? (enter to skip) ").strip()
    except EOFError:
        return None
    return answer or None


def _run_write(args, graph, cache, graph_dir, delta, origin: str, text: str) -> int:
    """Validate -> preview -> confirm -> write. Every write goes through here."""
    delta = delta_mod.drop_noops(delta_mod.normalize(delta), graph)
    if not delta:
        print("nothing to change")
        for item in delta.unmatched:
            print(f"  ? unmatched: {item}")
        return 0

    problems = delta_mod.validate(delta, graph)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2

    print("proposed:")
    _print_delta(graph, delta)
    print()
    result = queries.impact(graph, delta.overlay(), cache, delta.new_nodes)
    _print_impact(
        graph,
        result,
        getattr(args, "verbose", False),
        evidence_mod.gather(graph_dir, graph),
    )

    retreats = _retreats(graph, delta)
    if retreats:
        print()

    if args.dry_run:
        for node, before, after in retreats:
            print(f"  correction: {node} goes back from {before} to {after}")
        print("\ndry run - nothing written")
        return 0

    reason = getattr(args, "because", None)
    if retreats and not reason:
        reason = _ask_why(retreats)

    if not args.yes and not _confirm("\napply?"):
        print("not applied")
        return 1

    try:
        writes = edit.apply_delta(graph_dir, graph, delta)
    except edit.EditError as exc:
        print(f"error: {exc}\nnothing was written", file=sys.stderr)
        return 2
    journal.record(graph_dir, origin, text, writes, delta.unmatched, reason)
    cache.save()
    print(f"\nwrote {len(writes)} change(s) to {len({w.path for w in writes})} file(s)")
    return 0


def cmd_set(args) -> int:
    graph, cache, graph_dir = _load(args)
    requested = args.node
    args.node = _resolve(graph, requested)
    if args.node is None:
        return _unknown(graph, requested)
    overlay = _parse_set(args.node, args.assignments)
    changes = [
        delta_mod.ProposedChange(node_id, field, value)
        for node_id, fields in overlay.items()
        for field, value in fields.items()
    ]
    text = " ".join(args.assignments)
    delta = delta_mod.Delta(changes=changes, source=text)
    return _run_write(args, graph, cache, graph_dir, delta, "set", text)


def cmd_log(args) -> int:
    from . import propose as propose_mod

    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    try:
        delta = propose_mod.propose(graph, engine, args.text, cache)
    except (propose_mod.ProposalError, propose_mod.MissingCredentialsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _run_write(args, graph, cache, graph_dir, delta, "log", args.text)


def cmd_history(args) -> int:
    _graph, _cache, graph_dir = _load(args)
    entries = journal.read(graph_dir, args.limit)
    if args.json:
        _emit(entries, True)
        return 0
    if not entries:
        print("no history yet")
        return 0
    for entry in entries:
        print(f"{entry['at']}  [{entry['origin']}]  {entry['text']}")
        if entry.get("reason"):
            print(f"    why: {entry['reason']}")
        for write in entry.get("writes") or []:
            if write.get("created"):
                print(f"    + {write['node']} created")
            else:
                print(
                    f"    ~ {write['node']}  {write['field']}: "
                    f"{write['before']} -> {write['after']}"
                )
        for item in entry.get("unmatched") or []:
            print(f"    ? {item}")
    return 0


def cmd_trust(args) -> int:
    """Should you believe the declaration? Challenges only, never fixes."""
    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    derived = engine.all_derived()
    evidence = evidence_mod.gather(graph_dir, graph)
    cache.save()

    stale = evidence_mod.stale(graph, derived, evidence, args.stale_after)
    churn = evidence_mod.churning(evidence)
    unknown = [e for e in evidence.values() if e.last_change is None]

    claims = evidence_mod.edges(graph)
    unconfirmed = evidence_mod.unconfirmed(claims)
    aged = evidence_mod.stale_verifications(claims, args.stale_after)
    annotated, total_edges = evidence_mod.coverage(claims)
    corrected = journal.correction_counts(graph_dir)

    if args.json:
        _emit(
            {
                "stale": [e.as_dict() for e in stale],
                "churning": [e.as_dict() for e in churn],
                "unknown": [e.node for e in unknown],
                "unconfirmed_edges": [c.as_dict() for c in unconfirmed],
                "stale_verifications": [c.as_dict() for c in aged],
                "edge_coverage": {"annotated": annotated, "total": total_edges},
                "corrections": corrected,
            },
            True,
        )
        return 0

    if stale:
        print(
            f"stale - claims to be moving but has not changed in {args.stale_after}+ days:"
        )
        for item in stale:
            node = graph.get(item.node)
            print(
                f"  ! {item.node:<32} {node.status:<12} unchanged {item.age_days}d "
                f"(via {item.source})"
            )
    if churn:
        print("\nchurning - revised well above this graph's median:")
        for item in churn:
            shared = (
                f"  [shared with {item.shares_file_with} other node(s) in {item.path}]"
                if not item.precise
                else ""
            )
            print(f"  ~ {item.node:<32} {item.revisions} revisions{shared}")
    if unknown:
        print(
            f"\nno commits found for {len(unknown)} node(s) - the graph is in "
            f"git, so these are uncommitted"
            if evidence_mod.in_git(graph_dir)
            # The tool knows which of the two it is, so it says which rather
            # than offering a guess as a fact.
            else f"\nno history for {len(unknown)} node(s) - this graph is not in "
            f"a git repository"
        )
        for item in unknown[:5]:
            print(f"  ? {item.node}")
        if len(unknown) > 5:
            print(f"  ... and {len(unknown) - 5} more")

    if corrected:
        print("\ncorrected - previously declared wrong, and walked back:")
        for node_id, count in sorted(corrected.items(), key=lambda kv: (-kv[1], kv[0])):
            times = "once" if count == 1 else f"{count} times"
            print(f"  ! {node_id:<32} corrected {times}")
        print(
            "    (revision is negotiation; correction is error - weigh them differently)"
        )

    if unconfirmed:
        print("\nunconfirmed edges - believed, never checked:")
        for claim in unconfirmed:
            print(f"  ? {claim.source} -> {claim.target}   ({claim.how})")
    if aged:
        print("\nverified once, a while ago - the fact may have moved since:")
        for claim in aged:
            print(
                f"  ~ {claim.source} -> {claim.target}   "
                f"{claim.how} {claim.at} ({claim.age_days}d ago)"
            )

    if not journal.has_journal(graph_dir):
        # Without it, `trust` falls back to file-level git history and `drift`
        # has no baseline at all. Saying so beats quietly answering a weaker
        # question with the same confidence.
        print(
            "\nno journal for this graph, so answers here are weaker than they "
            "look:\n"
            "  - age is per file, not per node\n"
            "  - corrections and their reasons are unknown\n"
            "  - drift has no baseline to compare against\n"
            "one appears as soon as a change goes through `set` or `log`."
        )

    if total_edges:
        if evidence_mod.uses_provenance(claims):
            print(f"\nedge provenance: {annotated}/{total_edges} edges annotated")
        else:
            print(
                f"\nedge provenance: none of {total_edges} edges are annotated - add "
                f"`evidence:` to record which are checked and which are guesses"
            )

    anything = stale or churn or unknown or unconfirmed or aged or corrected
    if not anything:
        print("nothing to challenge - every declaration has history and none is stale")
    else:
        print(
            "\nthese are challenges, not corrections - nothing here changed any state"
        )
    return 0


# What to actually do about each finding. A code tells you what is true; a
# remedy tells you what to ask. `doctor` is meant to be run by an agent at the
# end of a bootstrap, so the objection has to arrive as an instruction.
REMEDIES = {
    "undrafted_contract": "nobody has agreed this. Ask both sides whether it is settled.",
    "unimplemented_contract": "who is building this? Add satisfied_by, or drop the gate.",
    "unconsumed_contract": "nothing needs this. Is it real, or left over?",
    "orphan_contract": "agreed with no implementer - who owns it?",
    "awaiting_decision": "who owes this call? nothing here is waiting on engineering.",
    "inert_node": "either connect it with a gate, or cut it. A list is not a graph.",
    "unowned_node": "which initiative is this part of? If none, who owns it?",
    "reaches_inside": "publish a fact on the subsystem and gate on that instead.",
    "gate_bypassed": "either the gate is wrong or the status is. Both cannot be right.",
    "working_ahead": "started before its gate opened - is the gate wrong,"
    " or is this at risk?",
    "parent_ahead_of_children": "closed while children are open. Which is true?",
    "rollup_lagging": "every child is done - can this be closed?",
    "frozen_unimplemented": "frozen before it was built. Unfreeze, or finish it.",
    "depends_on_abandoned": "this requires work that is gone. Re-point it or drop it.",
    "dead_evidence": "the edge this justified no longer exists - remove the evidence.",
    "drift": "edited outside trellis - reconcile it with `set`, or accept it.",
    "dead_acknowledgement": "this finding no longer fires - drop the acknowledge entry.",
    "legacy_journal": "move it into history/ and commit it; the cache stays ignored.",
    "cycle": "these depend on each other. One of the edges is wrong.",
    "dangling_reference": "names a node that does not exist. Typo, or not modelled yet?",
    "unreferenceable_id": "rename it with underscores; gates cannot reach it as it is.",
    "dangling_evidence": "evidence names a node that does not exist.",
    "self_reference": "a node cannot gate on itself.",
    "gate_error": "the expression does not evaluate - check the export names.",
    "publish_error": "the published expression does not evaluate.",
    "gate_parse_error": "the expression is not valid syntax.",
    "unknown_parent": "parent does not exist.",
    "unknown_implementer": "satisfied_by names a node that does not exist.",
    "duplicate_ref": "two nodes claim the same external item. Which one is it?",
    "shadowed_ref": "rename the ref or drop it - the node id wins, and always will.",
}


def cmd_doctor(args) -> int:
    """Everything that looks wrong, structural and evidential, in one place.

    Written to be the last step of a bootstrap: an agent runs this and reports
    what it says. The objections come from the tool, not from the agent's
    judgment, which is the only way they reliably arrive at all.
    """
    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    problems = queries.check(graph, engine, graph_dir)
    derived = engine.all_derived()
    evidence = evidence_mod.gather(graph_dir, graph)
    cache.save()

    stale = evidence_mod.stale(graph, derived, evidence, args.stale_after)
    claims = evidence_mod.edges(graph)
    unconfirmed = evidence_mod.unconfirmed(claims)
    aged = evidence_mod.stale_verifications(claims, args.stale_after)

    findings: list[tuple[str, str, str]] = []
    for problem in problems:  # check() ranks these already
        findings.append((problem.severity, problem.node, problem.message))
    for item in stale:
        findings.append(
            (
                "warn",
                item.node,
                f"claims to be moving but has not changed in {item.age_days} days",
            )
        )
    for item in journal.drift(graph_dir, graph):
        if item.is_correction:
            findings.append(
                (
                    "warn",
                    item.node,
                    f"was walked back from {item.journaled!r} to {item.actual!r} outside "
                    f"trellis - an unrecorded correction, and its reason is gone",
                )
            )
        else:
            findings.append(
                (
                    "info",
                    item.node,
                    f"changed outside trellis: it wrote {item.journaled!r}, the file "
                    f"says {item.actual!r}",
                )
            )
    for node_id, count in sorted(journal.correction_counts(graph_dir).items()):
        if count >= 2:
            findings.append(
                (
                    "warn",
                    node_id,
                    f"has been corrected {count} times - what it says now is worth "
                    f"less than what an uncorrected node says",
                )
            )
    for item in journal.corrections(graph_dir):
        if not item.reason:
            findings.append(
                (
                    "info",
                    item.node,
                    f"was corrected ({item.before} -> {item.after}) with no reason "
                    f"recorded - that lesson is gone",
                )
            )
    for claim in unconfirmed:
        findings.append(
            (
                "info",
                claim.source,
                f"its edge to {claim.target} is {claim.how} and was never confirmed",
            )
        )
    for claim in aged:
        findings.append(
            (
                "info",
                claim.source,
                f"its edge to {claim.target} was verified {claim.age_days} days ago "
                f"- that may have moved since",
            )
        )

    if args.json:
        _emit(
            [
                {"severity": sev, "node": node, "message": msg}
                for sev, node, msg in findings
            ],
            True,
        )
        return 1 if any(f[0] == "error" for f in findings) else 0

    if not findings:
        print(
            f"nothing looks wrong across {len(graph)} nodes.\n"
            "that is either a good graph or a graph too small to disagree with - "
            "if you just bootstrapped it, it is probably the second."
        )
        return 0

    codes = {p.node + p.message: p.code for p in problems}
    print(f"{len(findings)} thing(s) look wrong to me:\n")
    for severity, node, message in findings:
        mark = {"error": "!", "warn": "?", "info": "."}.get(severity, "-")
        print(f"  {mark} {node}: {message}")
        remedy = REMEDIES.get(codes.get(node + message, ""))
        if remedy:
            print(f"      -> {remedy}")
    print("\nnone of this changed any state. these are questions, not corrections.")
    print(
        "if one of these is true and will stay true, answer it for good with "
        "`acknowledge: [<code>]`\non the node - acknowledged findings are counted, "
        "not hidden."
    )
    return 1 if any(f[0] == "error" for f in findings) else 0


def cmd_drift(args) -> int:
    """Has anything been edited outside the loop since trellis last wrote it?"""
    graph, cache, graph_dir = _load(args)
    drifted = journal.drift(graph_dir, graph)
    cache.save()

    if args.accept and drifted:
        # Reconciling by re-running `set` does not work: the file already says
        # what you would be setting it to, so the change is a no-op and nothing
        # is journaled. Accepting is its own act — it records what the file now
        # says, and why, which is the part a hand edit threw away.
        writes = [
            edit.WriteResult(
                node=item.node,
                field="status",
                before=item.journaled,
                after=item.actual,
                path=str(graph_dir / graph.get(item.node).source),
            )
            for item in drifted
        ]
        journal.record(
            graph_dir,
            "accept",
            "accepted an edit made outside trellis",
            writes,
            reason=args.because,
        )
        print(f"accepted {len(drifted)} edit(s) into the journal.")
        for item in drifted:
            print(f"  {item.node}: {item.journaled!r} -> {item.actual!r}")
        if not args.because:
            print(
                "\nno reason recorded. `--because` is what keeps a correction from "
                "becoming\na thing that simply happened."
            )
        return 0

    if args.json:
        _emit([d.as_dict() for d in drifted], True)
        return 1 if drifted else 0

    if not drifted:
        managed = len(journal.last_written(graph_dir))
        if not managed:
            # An absence where we looked is not proof of absence anywhere.
            print(
                "nothing to compare against - no status writes are recorded in "
                "this graph's journal.\n"
                "drift is only detectable for nodes changed through `set` or `log`."
            )
        else:
            print(f"no drift - all {managed} node(s) trellis has written still agree")
        return 0

    corrections = [d for d in drifted if d.is_correction]
    print(f"{len(drifted)} node(s) changed outside trellis:\n")
    for item in drifted:
        mark = "!" if item.is_correction else "."
        print(
            f"  {mark} {item.node}: trellis wrote {item.journaled!r}, "
            f"file says {item.actual!r}"
        )
        print(f"      last written {item.at}")
    if corrections:
        print(
            f"\n{len(corrections)} of these walked a status backwards. Those are "
            "corrections,\nand the reason for each was never recorded."
        )
    print(
        "\ntrellis owns the state machine; edits made outside it are yours to "
        "reconcile.\n"
        "  trellis drift --accept --because '...'   record what the file now says\n"
        "  trellis set <node> status=...            change it back through the loop"
    )
    return 1


# Findings whose remedy is a status change trellis can make itself. Everything
# else routes to the editor: structural edits are not the writer's job, and
# pretending otherwise would guess at what someone meant.
DIRECT_FIX = {
    "rollup_lagging": ("status", "done", "close it - every child is done"),
}


def _open_editor(path, line: int) -> bool:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        print(f"  no $EDITOR set - the node is at {path}:{line}")
        return False
    try:
        # `+N` is understood by vi, vim, nano, emacs and most others; an editor
        # that does not simply opens the file.
        subprocess.call([editor, f"+{line}", str(path)])
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  could not open {editor}: {exc}")
        return False
    return True


def _review_one(args, graph, cache, graph_dir, engine, problem, index, total) -> str:
    """Show one finding and act on the answer. Returns the action taken."""
    print(f"\n[{index}/{total}] {problem.severity}  {problem.node}")
    for line in problem.message.splitlines():
        print(f"  {line.strip()}")
    remedy = REMEDIES.get(problem.code)
    if remedy:
        print(f"  -> {remedy}")

    choices = []
    fix = DIRECT_FIX.get(problem.code)
    if fix and problem.node in graph:
        choices.append(("f", f"fix ({fix[2]})"))
    if problem.severity != "error":
        choices.append(("a", "acknowledge"))
    choices += [("x", "explain"), ("e", "edit"), ("s", "skip"), ("q", "quit")]
    menu = "  ".join(f"[{key}] {label}" for key, label in choices)

    while True:
        print(f"\n  {menu}")
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return "quit"

        if answer in ("q", "quit"):
            return "quit"
        if answer in ("s", "skip", ""):
            return "skip"

        if answer in ("x", "explain"):
            reasons = queries.explain(engine, problem.node)
            if reasons:
                _print_reasons(reasons)
            else:
                print("  nothing gates this node")
            continue

        if answer in ("e", "edit"):
            path, line = edit.node_line(graph_dir, graph, problem.node)
            if _open_editor(path, line):
                return "edited"
            continue

        if answer in ("a", "acknowledge") and problem.severity != "error":
            try:
                reason = input("  why? (enter to skip) ").strip() or None
            except EOFError:
                reason = None
            try:
                write = edit.acknowledge(graph_dir, graph, problem.node, problem.code)
            except edit.EditError as exc:
                print(f"  error: {exc}")
                continue
            journal.record(
                graph_dir,
                "acknowledge",
                f"acknowledged {problem.code} on {problem.node}",
                [write],
                reason=reason,
            )
            print(f"  acknowledged {problem.code} on {problem.node}")
            return "acknowledged"

        if answer in ("f", "fix") and fix:
            field, value, _label = fix
            delta = delta_mod.Delta(
                changes=[delta_mod.ProposedChange(problem.node, field, value)],
                source=f"review: {problem.code}",
            )
            code = _run_write(
                args, graph, cache, graph_dir, delta, "review", delta.source
            )
            return "fixed" if code == 0 else "skip"

        print("  not one of the options")


def cmd_review(args) -> int:
    """Walk the findings one at a time, with something you can do about each.

    `doctor` hands you a list and leaves you to go and edit files. This is the
    same list as a session: the loop keeps the position, does what it can do
    safely, and puts you in the right file for everything else.
    """
    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    problems, muted = queries.check_with_muted(graph, engine, graph_dir)
    cache.save()

    if not sys.stdin.isatty():
        print(
            "error: review is interactive; run `trellis doctor` for the same "
            "findings as a report.",
            file=sys.stderr,
        )
        return 2

    if args.errors_only:
        problems = [p for p in problems if p.severity == "error"]

    if not problems:
        tail = f" ({muted} acknowledged)" if muted else ""
        print(f"nothing to review across {len(graph)} nodes{tail}")
        return 0

    print(f"{len(problems)} finding(s), most urgent first.")
    if muted:
        print(f"{muted} already acknowledged and not shown.")

    tally: dict[str, int] = {}
    live = {(p.node, p.code, p.message) for p in problems}
    for index, problem in enumerate(problems, 1):
        # A change may have resolved findings further down the list. Showing one
        # that no longer fires would be worse than not showing it: the whole
        # point is that these are true right now.
        if (problem.node, problem.code, problem.message) not in live:
            tally["resolved"] = tally.get("resolved", 0) + 1
            continue

        action = _review_one(
            args, graph, cache, graph_dir, engine, problem, index, len(problems)
        )
        tally[action] = tally.get(action, 0) + 1
        if action == "quit":
            break
        if action in ("acknowledged", "fixed", "edited"):
            # The graph on disk changed, so re-read it and recompute what is
            # still true. The position in the list is kept; the contents are not
            # assumed to be.
            graph, cache, graph_dir = _load(args)
            engine = Engine(graph, cache)
            live = {(p.node, p.code, p.message) for p in queries.check(graph, engine)}

    print()
    done = {k: v for k, v in tally.items() if k != "quit"}
    if done:
        print("  " + ", ".join(f"{v} {k}" for k, v in sorted(done.items())))
    remaining = len(problems) - sum(done.values())
    if remaining > 0:
        print(f"  {remaining} not reviewed")
    print("\nrun `trellis doctor` to see where that leaves things.")
    return 0


def cmd_blocking(args) -> int:
    """What is this holding up? Two numbers, because they answer two questions."""
    graph, cache, _ = _load(args)
    engine = Engine(graph, cache)

    if args.all:
        points = queries.chokepoints(engine, args.limit)
        cache.save()
        if args.json:
            _emit([b.as_dict() for b in points], True)
            return 0
        if not points:
            print("nothing is holding anything up")
            return 0
        print("holding up the most, open work only:\n")
        for item in points:
            print(
                f"  {item.node:<32} unlocks {len(item.unlocks):>2} now, "
                f"{len(item.waiting):>2} waiting downstream  {_title(graph, item.node)}"
            )
        print(
            "\n`unlocks` starts moving the moment it lands. `waiting` is everything "
            "downstream\nthat cannot start while it is open - most of that is also "
            "waiting on other things."
        )
        return 0

    requested = args.node
    args.node = _resolve(graph, requested)
    if args.node is None:
        return _unknown(graph, requested)
    result = queries.blocking(engine, args.node)
    cache.save()

    if args.json:
        _emit(result.as_dict(), True)
        return 0

    print(f"{args.node}  {_title(graph, args.node)}\n")
    if result.unlocks:
        print(f"unlocks {len(result.unlocks)} node(s) the moment it lands:")
        for node_id in result.unlocks:
            print(f"  > {node_id}  {_title(graph, node_id)}")
    else:
        print("unlocks nothing directly")
    downstream_only = [n for n in result.waiting if n not in result.unlocks]
    if downstream_only:
        print(
            f"\n{len(downstream_only)} more blocked downstream, also waiting on "
            f"other things:"
        )
        for node_id in downstream_only:
            print(f"  . {node_id}  {_title(graph, node_id)}")
    print(
        "\nthese are different questions. quoting the second number as the first "
        "is\nthe usual way this gets said wrong."
    )
    return 0


def cmd_graph(args) -> int:
    """Render a slice as mermaid, for pasting where someone else can read it."""
    graph, cache, _ = _load(args)
    engine = Engine(graph, cache)
    derived = engine.all_derived()

    if args.around:
        resolved = _resolve(graph, args.around)
        if resolved is None:
            return _unknown(graph, args.around)
        args.around = resolved

    nodes = viz.select(
        graph,
        around=args.around,
        hops=args.hops,
        contracts_only=args.contracts,
        blocked_only=args.blocked,
        derived=derived,
    )
    cache.save()

    if not nodes:
        print("nothing in that slice", file=sys.stderr)
        return 1
    if len(nodes) > args.max_nodes and not args.force:
        print(
            f"error: that slice is {len(nodes)} nodes; a diagram that big is not "
            f"readable.\n"
            f"narrow it with --around <node>, --contracts, or --blocked, "
            f"or pass --force.",
            file=sys.stderr,
        )
        return 2

    body = viz.mermaid(engine, nodes)
    if args.raw:
        print(body)
    else:
        print("```mermaid")
        print(body)
        print("```")
    return 0


def cmd_snapshot(args) -> int:
    """Freeze what the graph means right now, and render from it."""
    graph, cache, graph_dir = _load(args)

    if args.list:
        entries = snapshot_mod.read_index(graph_dir)
        if args.json:
            _emit([e.as_dict() for e in entries], True)
            return 0
        if not entries:
            print("no snapshots yet")
            return 0
        for entry in reversed(entries):  # newest first
            age = entry.age_days()
            # Age leads, because that is the only thing a snapshot cannot tell
            # you about itself once it is out in the world.
            when = f"{age}d ago" if age is not None else "unknown age"
            print(f"{when:>12}  {entry.id}  {entry.nodes} nodes", end="")
            if entry.graph_sha:
                print(f"  @{entry.graph_sha}", end="")
            print(f"  {entry.message}" if entry.message else "")
            for asset in entry.assets:
                print(f"                  {asset['path']}  ({asset['bytes']}b)")
        print(
            f"\n{len(entries)} snapshot(s). these are frozen; nothing refreshes them."
        )
        return 0

    if args.renderers:
        configured = snapshot_mod.load_renderers(graph_dir)
        names = sorted({*snapshot_mod.BUILTIN, *configured})
        for name in names:
            spec = configured.get(name)
            if spec:
                command = " ".join(spec["command"])
                print(f"  {name:<16} {command}")
            else:
                print(f"  {name:<16} (built in)")
        if not configured:
            print(
                f"\nadd more in {snapshot_mod.CONFIG_NAME}:\n"
                "  [renderer.brief]\n"
                '  command = ["your-tool", "--flag"]\n'
                '  extension = "md"'
            )
        return 0

    engine = Engine(graph, cache)
    try:
        entry, is_new = snapshot_mod.take(
            graph_dir,
            engine,
            renderers_wanted=args.render,
            message=args.message or "",
            force=args.force,
        )
    except snapshot_mod.SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cache.save()

    if not is_new:
        print(
            f"nothing has changed since {entry.id} - the derived state is "
            f"identical.\nuse --force to take one anyway."
        )
        return 0

    if args.json:
        _emit(entry.as_dict(), True)
        return 0

    print(f"{entry.id}")
    for asset in entry.assets:
        print(f"  {asset['path']}")
    print(
        f"\nfrozen at {entry.taken_at}. it is a snapshot: it will not update, and "
        f"it says\nnothing about the graph after this moment."
    )
    return 0


def cmd_deps(args) -> int:
    graph, _cache, _ = _load(args)
    requested = args.node
    args.node = _resolve(graph, requested)
    if args.node is None:
        return _unknown(graph, requested)
    if args.reverse:
        ids = graph.dependents_of(args.node)
        print(f"nodes that depend on {args.node}:")
    else:
        ids = graph.dependencies_of(args.node)
        print(f"{args.node} depends on:")
    if args.json:
        _emit(list(ids), True)
        return 0
    for node_id in ids:
        print(f"  {node_id}  {graph.get(node_id).title}")
    if not ids:
        print("  (none)")
    return 0


def cmd_stats(args) -> int:
    graph, cache, graph_dir = _load(args)
    engine = Engine(graph, cache)
    engine.all_derived()
    stats = cache.stats
    cache.save()
    payload = {
        "nodes": len(graph),
        "recomputed": len(engine.recomputed),
        "reused": len(engine.reused),
        "cache_entries": len(cache.entries),
        "cache_path": str(cache.path) if cache.path else None,
        **stats.as_dict(),
    }
    if args.json:
        _emit(payload, True)
        return 0
    print(f"graph        {graph_dir}")
    print(f"nodes        {payload['nodes']}")
    print(f"recomputed   {payload['recomputed']}")
    print(f"reused       {payload['reused']}")
    print(f"cache        {payload['cache_entries']} entries at {payload['cache_path']}")
    return 0


# -- entry point ------------------------------------------------------------


def _version() -> str:
    """Package version, plus the commit when running from a checkout.

    Installs come from git, so "the version" is whatever main was at the time.
    A bare version number would not identify a build well enough to act on a
    bug report.
    """
    from . import __version__

    here = Path(__file__).resolve().parent
    sha = None
    try:
        result = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return f"trellis {__version__}" + (f" ({sha})" if sha else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trellis",
        description="Compute project state from a graph of work and gates.",
    )
    parser.add_argument("--version", action="version", version=_version())
    parser.add_argument("--graph", help="path to the graph/ directory")
    parser.add_argument(
        "--no-cache", action="store_true", help="ignore the on-disk cache"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="validate the graph and list violations")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("state", help="derived state of the graph, or of one node")
    p.add_argument("node", nargs="?")
    p.add_argument("--ref", action="store_true", help="show each node's external id")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("ready", help="work whose start gate is satisfied")
    p.add_argument("--active", action="store_true", help="include in-progress work")
    p.set_defaults(func=cmd_ready)

    p = sub.add_parser("explain", help="why a node's gate is unmet, to root causes")
    p.add_argument("node")
    p.add_argument("--gate", default="start")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("impact", help="what-if: apply a change and diff the system")
    p.add_argument("node")
    p.add_argument(
        "--set",
        action="append",
        metavar="FIELD=VALUE",
        help="field to change (default status=done); prefix with NODE@ "
        "to target another node",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("deps", help="dependencies of a node")
    p.add_argument("node")
    p.add_argument(
        "-r", "--reverse", action="store_true", help="show dependents instead"
    )
    p.set_defaults(func=cmd_deps)

    p = sub.add_parser("set", help="change a node's declared state, with a preview")
    p.add_argument("node")
    p.add_argument(
        "assignments",
        nargs="+",
        metavar="FIELD=VALUE",
        help="prefix with NODE@ to change another node in the same delta",
    )
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--because",
        metavar="REASON",
        help="why - recorded in the journal. Asked for automatically on a correction",
    )
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("log", help="describe what happened; a model proposes the delta")
    p.add_argument(
        "text", help='e.g. "finished the sandbox work, schema is signed off"'
    )
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--because", metavar="REASON", help="why - recorded in the journal")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("history", help="what has been applied, and why")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser(
        "trust", help="challenge the declaration: what is stale, what churns"
    )
    p.add_argument(
        "--stale-after",
        type=int,
        default=evidence_mod.DEFAULT_STALE_DAYS,
        metavar="DAYS",
        help="age at which a moving declaration is challenged "
        f"(default {evidence_mod.DEFAULT_STALE_DAYS})",
    )
    p.set_defaults(func=cmd_trust)

    p = sub.add_parser(
        "drift", help="what has been edited outside trellis since it last wrote"
    )
    p.add_argument(
        "--accept",
        action="store_true",
        help="record the current file state in the journal, ending the drift",
    )
    p.add_argument("--because", metavar="REASON", help="why the edit was made")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser(
        "doctor", help="everything that looks wrong, structural and evidential"
    )
    p.add_argument(
        "--stale-after",
        type=int,
        default=evidence_mod.DEFAULT_STALE_DAYS,
        metavar="DAYS",
    )
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("snapshot", help="freeze what the graph means right now")
    p.add_argument(
        "--render",
        action="append",
        metavar="NAME",
        help="also produce this artifact; repeatable",
    )
    p.add_argument("-m", "--message", help="why you took it")
    p.add_argument(
        "--list", action="store_true", help="what has been taken, newest first"
    )
    p.add_argument("--renderers", action="store_true", help="what can be rendered")
    p.add_argument(
        "--force", action="store_true", help="take one even if nothing has changed"
    )
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("blocking", help="what a node is holding up, by both measures")
    p.add_argument("node", nargs="?")
    p.add_argument(
        "--all", action="store_true", help="rank every open node by what it holds up"
    )
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_blocking)

    p = sub.add_parser("graph", help="render a slice as mermaid")
    p.add_argument("--around", metavar="NODE", help="centre the slice on one node")
    p.add_argument("--hops", type=int, default=1, help="how far from --around")
    p.add_argument(
        "--contracts", action="store_true", help="contracts and who touches them"
    )
    p.add_argument("--blocked", action="store_true", help="only what is not moving")
    p.add_argument("--max-nodes", type=int, default=25)
    p.add_argument("--force", action="store_true", help="render it anyway")
    p.add_argument("--raw", action="store_true", help="omit the markdown fence")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("review", help="walk the findings one at a time and act on them")
    p.add_argument(
        "--errors-only", action="store_true", help="only findings that block evaluation"
    )
    p.add_argument("-y", "--yes", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-n", "--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("stats", help="cache and recomputation counters")
    p.set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CycleError as exc:
        # Every command that reads derived state hits this, so it is handled
        # once here rather than in each. Derived state is undefined until the
        # cycle is cut, and `check` is the command that explains the shape.
        print(
            f"error: {exc}\n"
            "derived state is undefined until that is cut. "
            "run `trellis check` - it names the cause when it recognises the shape.",
            file=sys.stderr,
        )
        return 2
    except (ModelError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
