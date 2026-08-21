"""An append-only record of every applied change.

The YAML files hold the current state; git holds the diffs. Neither holds the
*why* — the sentence you typed that produced the change, or the fact that a
model proposed it and you accepted it. That is the part worth keeping when you
come back in three weeks asking how the graph got into this shape.

One JSON object per line, appended, never rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .loader import project_root
from .model import Graph, is_retreat

JOURNAL_NAME = "journal.jsonl"
HISTORY_DIRNAME = "history"
# Where the journal used to live, back when durable history and a disposable
# cache shared a directory because they were written at the same afternoon
# rather than because they are the same kind of thing.
LEGACY_DIRNAME = ".trellis"


def journal_path(graph_dir: str | Path) -> Path:
    """Where the journal is written. Committed, on purpose.

    The journal is the only copy of *why* — the reason on every correction,
    what was acknowledged and what for, and the baseline `drift` compares
    against. None of that is recoverable from the YAML or from git, so it
    cannot live somewhere that never leaves one machine.
    """
    return project_root(graph_dir) / HISTORY_DIRNAME / JOURNAL_NAME


def legacy_journal_paths(graph_dir: str | Path) -> list[Path]:
    """Everywhere a journal may have been written before it had one home.

    Two locations, not one. Before the path fix, the journal was placed
    relative to how `--graph` was *spelled*, so anyone who ran `--graph .` from
    inside their graph directory has one a level deeper than the resolved
    parent. Looking only where it should have been would lose exactly the
    people the path fix was about.
    """
    candidates = [
        project_root(graph_dir) / LEGACY_DIRNAME / JOURNAL_NAME,
        Path(graph_dir).resolve() / LEGACY_DIRNAME / JOURNAL_NAME,
    ]
    seen, out = set(), []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def legacy_journal_path(graph_dir: str | Path) -> Path:
    """The first legacy journal that exists, or the canonical old location."""
    paths = legacy_journal_paths(graph_dir)
    return next((p for p in paths if p.exists()), paths[0])


def has_journal(graph_dir: str | Path) -> bool:
    return journal_path(graph_dir).exists() or any(
        p.exists() for p in legacy_journal_paths(graph_dir)
    )


def record(
    graph_dir: str | Path,
    origin: str,
    text: str,
    writes: list,
    unmatched: list[str] | None = None,
    reason: str | None = None,
) -> Path:
    """Append one entry. `origin` is how the change arrived: `set` or `log`."""
    path = journal_path(graph_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "origin": origin,
        "text": text,
        "reason": reason,
        "writes": [w.as_dict() for w in writes],
        "unmatched": unmatched or [],
    }
    with path.open("a") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")
    return path


def _read_one(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def read(graph_dir: str | Path, limit: int | None = None) -> list[dict]:
    """Most recent entries last. Malformed lines are skipped, not fatal.

    Reads the old location as well as the current one, so moving the file is
    something you do when you get to it rather than something that silently
    loses the reasons you already recorded. Legacy entries come first: they
    predate the new file by construction.
    """
    entries: list[dict] = []
    for legacy in legacy_journal_paths(graph_dir):
        entries += _read_one(legacy)
    entries += _read_one(journal_path(graph_dir))
    return entries[-limit:] if limit else entries


@dataclass
class Outcome:
    """What checking one believed edge turned out to be.

    Recorded because it is not recoverable afterwards. A confirmed edge becomes
    `verified` in the YAML and a wrong one gets rewritten or deleted — both
    structural edits, both unjournaled, and the wrong case usually **deletes the
    evidence of its own failure**, which is the datum worth keeping.
    """

    source: str
    target: str
    how: str
    held: bool
    at: str = ""
    reason: str = ""

    @property
    def edge(self) -> tuple[str, str]:
        return (self.source, self.target)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "how": self.how,
            "held": self.held,
            "reason": self.reason,
        }


def record_outcome(
    graph_dir: str | Path, outcomes: list[Outcome], reason: str | None = None
) -> Path:
    """Append what a reconciliation pass found.

    Kept in the journal rather than beside the edge, so it survives the edge
    being deleted — which is exactly what happens to an edge that turned out to
    be wrong.
    """
    path = journal_path(graph_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrong = sum(1 for o in outcomes if not o.held)
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "origin": "reconcile",
        "text": f"checked {len(outcomes)} edge(s), {wrong} wrong",
        "reason": reason,
        "writes": [],
        "outcomes": [o.as_dict() for o in outcomes],
    }
    with path.open("a") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")
    return path


def outcomes(graph_dir: str | Path) -> list[Outcome]:
    """Every recorded reconciliation outcome, oldest first."""
    out: list[Outcome] = []
    for entry in read(graph_dir):
        at = entry.get("at", "")
        for item in entry.get("outcomes") or []:
            out.append(
                Outcome(
                    source=item.get("source", ""),
                    target=item.get("target", ""),
                    how=item.get("how", ""),
                    held=bool(item.get("held")),
                    at=at,
                    reason=item.get("reason", "") or entry.get("reason") or "",
                )
            )
    return out


def reconciled(graph_dir: str | Path) -> dict[tuple[str, str], Outcome]:
    """The most recent outcome per edge."""
    return {o.edge: o for o in outcomes(graph_dir)}


def calibration(graph_dir: str | Path) -> tuple[int, int]:
    """(checked, wrong) across every recorded outcome.

    Returned as counts, never a rate. Small denominators lie, and 2 of 7 is
    honest where 29% invites being read as a property of the world.
    """
    all_outcomes = outcomes(graph_dir)
    return len(all_outcomes), sum(1 for o in all_outcomes if not o.held)


@dataclass
class Correction:
    """A declaration that walked backwards: a belief revised, not progress."""

    node: str
    at: str
    before: object
    after: object
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "at": self.at,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
        }


def corrections(graph_dir: str | Path) -> list[Correction]:
    """Every recorded correction, oldest first.

    A correction is worth more than a revision. Revision is negotiation — a
    contract argued over four times is being worked out. Correction is error —
    a node declared done twice and walked back twice was wrong twice, and that
    is a much stronger reason to distrust what it says now.
    """
    out: list[Correction] = []
    for entry in read(graph_dir):
        for write in entry.get("writes") or []:
            if write.get("field") != "status":
                continue
            if not is_retreat(write.get("before"), write.get("after")):
                continue
            out.append(
                Correction(
                    node=write.get("node", ""),
                    at=entry.get("at", ""),
                    before=write.get("before"),
                    after=write.get("after"),
                    reason=entry.get("reason"),
                )
            )
    return out


def correction_counts(graph_dir: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in corrections(graph_dir):
        counts[item.node] = counts.get(item.node, 0) + 1
    return counts


@dataclass
class Drift:
    """A node whose file disagrees with the last thing trellis wrote to it.

    trellis owns the state machine. Editing a status by hand is allowed and
    sometimes the right thing, but it happens outside the loop: no preview, no
    verification, no journal entry, and — when the edit walks a status
    backwards — no recorded reason. That is drift, and it is the editor's to
    own. All this does is refuse to let it stay invisible.
    """

    node: str
    journaled: object
    actual: object
    at: str

    @property
    def is_correction(self) -> bool:
        """Whether the hand edit walked the status backwards.

        Worse than ordinary drift: a correction carries a lesson, and this one
        was never written down.
        """
        return is_retreat(self.journaled, self.actual)

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "journaled": self.journaled,
            "actual": self.actual,
            "at": self.at,
            "is_correction": self.is_correction,
        }


def last_written(graph_dir: str | Path) -> dict[str, tuple[object, str]]:
    """The last status trellis itself wrote for each node, and when."""
    out: dict[str, tuple[object, str]] = {}
    for entry in read(graph_dir):
        for write in entry.get("writes") or []:
            if write.get("field") != "status":
                continue
            node = write.get("node")
            if node:
                out[node] = (write.get("after"), entry.get("at", ""))
    return out


def drift(graph_dir: str | Path, graph: Graph) -> list[Drift]:
    """Nodes edited outside the loop since trellis last wrote them.

    Only nodes trellis has actually written are considered. A node that has
    never been through `set` or `log` is not drifting — it is simply not being
    managed through the tool, which is a different thing and not a complaint.
    """
    out: list[Drift] = []
    for node_id, (written, at) in last_written(graph_dir).items():
        if node_id not in graph:
            continue
        actual = graph.get(node_id).status
        if actual != written:
            out.append(Drift(node=node_id, journaled=written, actual=actual, at=at))
    return sorted(out, key=lambda d: (not d.is_correction, d.node))
