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

from .model import is_retreat

JOURNAL_NAME = "journal.jsonl"


def journal_path(graph_dir: str | Path) -> Path:
    return Path(graph_dir).parent / ".trellis" / JOURNAL_NAME


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


def read(graph_dir: str | Path, limit: int | None = None) -> list[dict]:
    """Most recent entries last. Malformed lines are skipped, not fatal."""
    path = journal_path(graph_dir)
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
    return entries[-limit:] if limit else entries


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
