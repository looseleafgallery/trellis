"""Evidence about the declaration itself, rather than about the work.

The engine answers "what does the graph say". This module answers the question
that decides whether anyone keeps using it: **should you believe what the graph
says?** A confidently wrong graph is worse than no graph, because it is not
obviously absent.

Two signals, both free, both requiring no integration with anything:

- **Volatility** — how many times a node's declaring file has been revised.
  A node at 16 revisions against a median of 4 is not the same node as one
  written once and left alone, even when both currently say `agreed`. This is
  what turns "unlocks three nodes" (mechanical) into "unlocks a node whose
  contract has been renegotiated four times" (a decision).
- **Age** — how long a declaration has sat unchanged. `in_progress` untouched
  for three weeks is the single most common way a graph goes quietly wrong.

Both **challenge, never set.** Nothing here changes a status or opens a gate.

Why this is a separate layer rather than part of derived state: age depends on
the wall clock, and the engine's cache key is a hash of its inputs. Letting
"today" into that hash would mean every entry silently expiring at midnight, or
worse, a cache hit returning yesterday's answer. The engine stays time-free and
exactly cacheable; everything time-dependent lives out here and is recomputed
per query, which is cheap because it is one git call.
"""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import journal
from .model import Graph

# A declaration older than this, on work that claims to be moving, gets
# challenged. Two weeks is a guess; it is a flag on every command that uses it.
DEFAULT_STALE_DAYS = 14
# Below this many nodes with history, volatility is not classified at all.
MIN_SAMPLE = 4


@dataclass
class Evidence:
    """What we can tell about a node's declaration without asking anyone."""

    node: str
    path: str
    # Revisions of the declaring file. Shared when a file holds several nodes.
    revisions: int | None = None
    shares_file_with: int = 0
    last_change: datetime | None = None
    # Where last_change came from: the journal knows per node, git per file.
    source: str = "unknown"
    age_days: int | None = None
    band: str = "unknown"  # settled | typical | churning

    @property
    def precise(self) -> bool:
        """Whether the revision count is about this node alone."""
        return self.shares_file_with == 0

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "path": self.path,
            "revisions": self.revisions,
            "shares_file_with": self.shares_file_with,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "source": self.source,
            "age_days": self.age_days,
            "band": self.band,
        }


def _git(graph_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(graph_dir), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def head_sha(graph_dir: str | Path) -> str | None:
    """The commit the graph is currently at, if it is in a repo at all."""
    out = _git(Path(graph_dir), "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def file_history(graph_dir: str | Path) -> dict[str, tuple[int, datetime]]:
    """Revision count and last-changed per file, in one git call.

    Returns paths relative to the graph directory. Empty when the graph is not
    in a git repository — every signal here degrades to "unknown" rather than
    to a wrong answer.
    """
    graph_dir = Path(graph_dir)
    root = _git(graph_dir, "rev-parse", "--show-toplevel")
    if not root:
        return {}
    repo_root = Path(root.strip())

    # One log for the whole directory: a NUL-prefixed date line per commit,
    # followed by the paths that commit touched.
    out = _git(
        graph_dir,
        "log",
        "--format=%x00%cI",
        "--name-only",
        "--",
        str(graph_dir),
    )
    if not out:
        return {}

    counts: dict[str, int] = {}
    latest: dict[str, datetime] = {}
    current: datetime | None = None
    for line in out.splitlines():
        if line.startswith("\x00"):
            try:
                current = datetime.fromisoformat(line[1:].strip())
            except ValueError:
                current = None
            continue
        path = line.strip()
        if not path or current is None:
            continue
        try:
            relative = str(
                (repo_root / path).resolve().relative_to(graph_dir.resolve())
            )
        except ValueError:
            continue  # touched something outside the graph directory
        counts[relative] = counts.get(relative, 0) + 1
        if relative not in latest or current > latest[relative]:
            latest[relative] = current
    return {path: (counts[path], latest[path]) for path in counts}


def _journal_last_change(graph_dir: str | Path) -> dict[str, datetime]:
    """Per-node last write, from the journal. More precise than file history."""
    out: dict[str, datetime] = {}
    for entry in journal.read(graph_dir):
        try:
            at = datetime.fromisoformat(entry["at"])
        except (KeyError, ValueError):
            continue
        for write in entry.get("writes") or []:
            node = write.get("node")
            if not node:
                continue
            if node not in out or at > out[node]:
                out[node] = at
    return out


def gather(
    graph_dir: str | Path, graph: Graph, now: datetime | None = None
) -> dict[str, Evidence]:
    """Collect what is knowable about every node's declaration."""
    graph_dir = Path(graph_dir)
    now = now or datetime.now(UTC)
    history = file_history(graph_dir)
    journal_changes = _journal_last_change(graph_dir)

    nodes_per_file: dict[str, int] = {}
    for node in graph:
        nodes_per_file[node.source] = nodes_per_file.get(node.source, 0) + 1

    out: dict[str, Evidence] = {}
    for node in graph:
        revisions, file_change = history.get(node.source, (None, None))
        # The journal knows which *node* changed; git only knows which file.
        # Prefer the journal, fall back to the file, and say which was used.
        node_change = journal_changes.get(node.id)
        if node_change and (not file_change or node_change >= file_change):
            last_change, source = node_change, "journal"
        elif file_change:
            last_change, source = file_change, "git"
        else:
            last_change, source = None, "unknown"

        out[node.id] = Evidence(
            node=node.id,
            path=node.source,
            revisions=revisions,
            shares_file_with=max(0, nodes_per_file.get(node.source, 1) - 1),
            last_change=last_change,
            source=source,
            age_days=max(0, (now - last_change).days) if last_change else None,
        )

    _band(out)
    return out


def _band(evidence: dict[str, Evidence]) -> None:
    """Classify volatility against this graph's own median, not an absolute.

    What counts as churn is relative: a graph revised daily and one revised
    quarterly have different normals, and an absolute threshold would be wrong
    for both.
    """
    counts = [e.revisions for e in evidence.values() if e.revisions is not None]
    # An outlier needs a baseline to be an outlier against. With a handful of
    # nodes the churning one dominates its own median, so say nothing rather
    # than saying something unfounded.
    if len(counts) < MIN_SAMPLE:
        return
    median = statistics.median(counts)
    for item in evidence.values():
        if item.revisions is None:
            continue
        if item.revisions >= max(median * 2, median + 2):
            item.band = "churning"
        elif item.revisions <= max(1, median / 2):
            item.band = "settled"
        else:
            item.band = "typical"


def churning(evidence: dict[str, Evidence]) -> list[Evidence]:
    return sorted(
        (e for e in evidence.values() if e.band == "churning"),
        key=lambda e: (-(e.revisions or 0), e.node),
    )


def stale(
    graph: Graph,
    derived: dict,
    evidence: dict[str, Evidence],
    max_age_days: int = DEFAULT_STALE_DAYS,
) -> list[Evidence]:
    """Declarations that claim to be moving but have not moved.

    Only work that asserts motion is challenged. A node sitting at
    `not_started` for a year is not stale — it is accurate.
    """
    out = []
    for node_id, item in evidence.items():
        if item.age_days is None or item.age_days < max_age_days:
            continue
        node = graph.get(node_id)
        if node.kind == "work" and node.status == "in_progress":
            out.append(item)
        elif node.kind == "work" and node.awaiting:
            # Same rule as an undecided contract: nobody is making this call.
            out.append(item)
        elif node.kind == "contract" and node.status in ("draft", "proposed"):
            # An agreement nobody has touched in weeks is a decision nobody is
            # making, which is exactly the thing that quietly holds up a chain.
            out.append(item)
    return sorted(out, key=lambda e: (-(e.age_days or 0), e.node))


# -- provenance -------------------------------------------------------------


@dataclass
class EdgeClaim:
    """One edge, with whatever is known about why it is believed."""

    source: str
    target: str
    how: str | None = None
    at: str | None = None
    age_days: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.how in ("verified", "stated")

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "how": self.how,
            "at": self.at,
            "age_days": self.age_days,
        }


def edges(graph: Graph, now: datetime | None = None) -> list[EdgeClaim]:
    """Every dependency edge, annotated with its declared provenance."""
    now = now or datetime.now(UTC)
    out: list[EdgeClaim] = []
    for node in graph:
        declared = node.evidence_map
        for target in graph.references_of(node.id):
            item = declared.get(target)
            age = None
            if item is not None and item.at:
                try:
                    stamped = datetime.fromisoformat(item.at)
                    if stamped.tzinfo is None:
                        stamped = stamped.replace(tzinfo=UTC)
                    age = max(0, (now - stamped).days)
                except ValueError:
                    age = None
            out.append(
                EdgeClaim(
                    source=node.id,
                    target=target,
                    how=item.how if item else None,
                    at=item.at if item else None,
                    age_days=age,
                )
            )
    return sorted(out, key=lambda e: (e.source, e.target))


def unconfirmed(claims: list[EdgeClaim]) -> list[EdgeClaim]:
    """Edges believed on inference or assumption — standing invitations to check."""
    return [c for c in claims if c.how in ("inferred", "assumed")]


def stale_verifications(
    claims: list[EdgeClaim], max_age_days: int = DEFAULT_STALE_DAYS
) -> list[EdgeClaim]:
    """Edges checked once, a while ago.

    A verification has a shelf life: it was true against the thing it was
    checked against, at that moment, and that thing can move afterwards without
    anything here noticing.
    """
    return sorted(
        (
            c
            for c in claims
            if c.confirmed and c.age_days is not None and c.age_days >= max_age_days
        ),
        key=lambda c: -(c.age_days or 0),
    )


def coverage(claims: list[EdgeClaim]) -> tuple[int, int]:
    """How many edges carry provenance at all."""
    return sum(1 for c in claims if c.how), len(claims)


def uses_provenance(claims: list[EdgeClaim]) -> bool:
    """Whether this graph annotates edges at all.

    Gaps only mean something once someone has started annotating. Until then,
    reporting every unannotated edge would be pure noise.
    """
    return any(c.how for c in claims)
