"""Point-in-time captures of derived state.

Git already has your YAML. What it does not have — and what cannot be
recovered by checking out an old commit — is what the graph *meant* at a
moment. Derived state depends on the engine, and the trust layer reads today's
git history: volatility, staleness and drift are all computed against now. Run
`check` against a three-week-old checkout and you get today's answers about
three-week-old files, which is nobody's question.

So a snapshot is the only way to know what you knew then.

**It is called a snapshot because that is a promise.** It is frozen on purpose,
it is stale the moment after it is taken, and freshness is the reader's problem
— which is exactly why it is safe to have in a tool whose whole thesis is
failing loudly when something is out of date. Nothing here ever refreshes a
snapshot in place, and nothing presents one as current. `state` and `doctor`
answer for now; this answers for then.

Snapshots are content-addressed by the derived state they capture, so taking
one twice from an unchanged graph is recognised rather than duplicated — and so
a future timeline over them is an index query rather than a rebuild.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import evidence as evidence_mod
from . import journal, queries, viz
from .engine import ENGINE_VERSION, Engine
from .loader import project_root

SNAPSHOT_DIRNAME = "snapshots"
INDEX_NAME = "index.jsonl"
CONFIG_NAME = "trellis.toml"


class SnapshotError(RuntimeError):
    """A snapshot could not be taken or rendered."""


def snapshot_dir(graph_dir: str | Path) -> Path:
    # A sibling of `graph/`, not inside `.trellis/`: snapshots are deliberate
    # artefacts someone asked for, and are meant to be committed and shared.
    return project_root(graph_dir) / SNAPSHOT_DIRNAME


def _version() -> str:
    from . import __version__

    return __version__


def _now() -> datetime:
    return datetime.now(UTC)


def _state_hash(engine: Engine) -> str:
    """Content address of the whole derived state.

    Built from each node's cache key, which is already a hash of everything
    that can change its derived value — so this is exact rather than a
    best-effort digest.
    """
    derived = engine.all_derived()
    payload = sorted(f"{nid}:{d.key}" for nid, d in derived.items())
    return hashlib.sha256("\n".join(payload).encode()).hexdigest()[:16]


def capture(graph_dir: str | Path, engine: Engine, message: str = "") -> dict:
    """Everything worth knowing about the graph at this moment."""
    graph = engine.graph
    derived = engine.all_derived()
    problems, muted = queries.check_with_muted(graph, engine)
    ev = evidence_mod.gather(graph_dir, graph)
    claims = evidence_mod.edges(graph)

    return {
        "meta": {
            "taken_at": _now().isoformat(timespec="seconds"),
            "message": message,
            "state_hash": _state_hash(engine),
            "graph_sha": evidence_mod.head_sha(graph_dir),
            "nodes": len(graph),
            "engine_version": ENGINE_VERSION,
            "trellis_version": _version(),
        },
        "summary": queries.summary(engine),
        "nodes": {nid: d.as_dict() for nid, d in derived.items()},
        "titles": {n.id: n.title for n in graph},
        "findings": [p.as_dict() for p in problems],
        "acknowledged": muted,
        "trust": {
            "stale": [e.as_dict() for e in evidence_mod.stale(graph, derived, ev)],
            "churning": [e.as_dict() for e in evidence_mod.churning(ev)],
            "unconfirmed_edges": [
                c.as_dict() for c in evidence_mod.unconfirmed(claims)
            ],
            "stale_verifications": [
                c.as_dict() for c in evidence_mod.stale_verifications(claims)
            ],
            "drift": [d.as_dict() for d in journal.drift(graph_dir, graph)],
            "corrections": journal.correction_counts(graph_dir),
        },
    }


# -- the index ---------------------------------------------------------------


@dataclass
class Entry:
    """One line of the index: a snapshot and the assets rendered from it."""

    id: str
    taken_at: str
    state_hash: str
    graph_sha: str | None
    message: str
    nodes: int
    assets: list[dict] = field(default_factory=list)

    def age_days(self, now: datetime | None = None) -> int | None:
        try:
            taken = datetime.fromisoformat(self.taken_at)
        except ValueError:
            return None
        return max(0, ((now or _now()) - taken).days)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "taken_at": self.taken_at,
            "state_hash": self.state_hash,
            "graph_sha": self.graph_sha,
            "message": self.message,
            "nodes": self.nodes,
            "assets": self.assets,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Entry:
        return cls(
            id=data["id"],
            taken_at=data.get("taken_at", ""),
            state_hash=data.get("state_hash", ""),
            graph_sha=data.get("graph_sha"),
            message=data.get("message", ""),
            nodes=data.get("nodes", 0),
            assets=data.get("assets") or [],
        )


def read_index(graph_dir: str | Path) -> list[Entry]:
    """Oldest first. A malformed line is skipped, never fatal."""
    path = snapshot_dir(graph_dir) / INDEX_NAME
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Entry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def _append_index(graph_dir: str | Path, entry: Entry) -> None:
    path = snapshot_dir(graph_dir) / INDEX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(entry.as_dict()) + "\n")


# -- renderers ---------------------------------------------------------------


BUILTIN = ("json", "mermaid")


def load_renderers(graph_dir: str | Path) -> dict[str, dict]:
    """External renderers declared in trellis.toml, if there is one."""
    path = project_root(graph_dir) / CONFIG_NAME
    if not path.exists():
        return {}
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise SnapshotError(f"{CONFIG_NAME}: {exc}") from exc

    renderers = config.get("renderer") or {}
    if not isinstance(renderers, dict):
        raise SnapshotError(f"{CONFIG_NAME}: [renderer.<name>] tables expected")
    for name, spec in renderers.items():
        if name in BUILTIN:
            raise SnapshotError(
                f"{CONFIG_NAME}: {name!r} is a built-in renderer; pick another name"
            )
        command = spec.get("command")
        if not isinstance(command, list) or not command:
            raise SnapshotError(
                f'{CONFIG_NAME}: renderer {name!r} needs command = ["prog", ...]'
            )
    return renderers


def render(
    name: str,
    payload: dict,
    engine: Engine,
    renderers: dict[str, dict],
    timeout: int = 60,
) -> tuple[bytes, str]:
    """Produce one artifact. Returns (bytes, file extension).

    External renderers are run as a subprocess with the snapshot on stdin. They
    are never handed a path to the graph, so a renderer *cannot* modify source
    — that is a property of the interface rather than a rule anyone has to
    follow.
    """
    if name == "json":
        return json.dumps(payload, indent=2).encode(), "json"
    if name == "mermaid":
        nodes = set(engine.graph.ids())
        return viz.mermaid(engine, nodes).encode(), "mmd"

    spec = renderers.get(name)
    if spec is None:
        known = ", ".join(sorted({*BUILTIN, *renderers}))
        raise SnapshotError(f"unknown renderer {name!r}; available: {known}")

    try:
        result = subprocess.run(
            spec["command"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SnapshotError(
            f"renderer {name!r}: {spec['command'][0]!r} not found"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"renderer {name!r} timed out after {timeout}s") from exc

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or "no output"
        raise SnapshotError(f"renderer {name!r} exited {result.returncode}: {detail}")
    if not result.stdout:
        raise SnapshotError(f"renderer {name!r} produced nothing")
    return result.stdout, str(spec.get("extension", "txt")).lstrip(".")


# -- taking one --------------------------------------------------------------


def take(
    graph_dir: str | Path,
    engine: Engine,
    renderers_wanted: list[str] | None = None,
    message: str = "",
    force: bool = False,
) -> tuple[Entry, bool]:
    """Write a snapshot and any requested artifacts. Returns (entry, is_new).

    If the derived state is identical to the most recent snapshot, nothing is
    written unless forced — the point of content addressing is that taking the
    same picture twice is recognisable rather than duplicated.
    """
    configured = load_renderers(graph_dir)
    # Validated before anything else: asking for an artifact that cannot be
    # produced should fail, not be quietly skipped because the state happened
    # to be unchanged.
    for name in renderers_wanted or []:
        if name not in BUILTIN and name not in configured:
            known = ", ".join(sorted({*BUILTIN, *configured}))
            raise SnapshotError(f"unknown renderer {name!r}; available: {known}")

    payload = capture(graph_dir, engine, message)
    state_hash = payload["meta"]["state_hash"]

    existing = read_index(graph_dir)
    if existing and existing[-1].state_hash == state_hash and not force:
        already = {a["renderer"] for a in existing[-1].assets}
        missing = [n for n in (renderers_wanted or []) if n not in already]
        if missing:
            raise SnapshotError(
                f"nothing has changed since {existing[-1].id}, but it has no "
                f"{', '.join(missing)} artifact. Use --force to take a new "
                f"snapshot with it."
            )
        return existing[-1], False

    # Filename-safe and sortable. The full timestamp with its offset stays in
    # the payload and the index; this is only the id.
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{stamp}-{state_hash[:8]}"
    target = snapshot_dir(graph_dir) / snapshot_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "snapshot.json").write_text(json.dumps(payload, indent=2) + "\n")

    assets = [
        {
            "name": "snapshot",
            "renderer": "json",
            "path": f"{snapshot_id}/snapshot.json",
            "bytes": (target / "snapshot.json").stat().st_size,
        }
    ]

    for name in renderers_wanted or []:
        body, extension = render(name, payload, engine, configured)
        filename = f"{name}.{extension}"
        (target / filename).write_bytes(body)
        assets.append(
            {
                "name": name,
                "renderer": name,
                "path": f"{snapshot_id}/{filename}",
                "bytes": len(body),
                # Repeated per asset on purpose: an artifact that travels away
                # from the index still has to say when it was true.
                "taken_at": payload["meta"]["taken_at"],
            }
        )

    entry = Entry(
        id=snapshot_id,
        taken_at=payload["meta"]["taken_at"],
        state_hash=state_hash,
        graph_sha=payload["meta"]["graph_sha"],
        message=message,
        nodes=payload["meta"]["nodes"],
        assets=assets,
    )
    _append_index(graph_dir, entry)
    return entry, True


def load(graph_dir: str | Path, snapshot_id: str) -> dict:
    path = snapshot_dir(graph_dir) / snapshot_id / "snapshot.json"
    if not path.exists():
        raise SnapshotError(f"no snapshot {snapshot_id!r}")
    return json.loads(path.read_text())
