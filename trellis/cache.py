"""Persistent memo store for derived node state.

Keys are content hashes (see engine.cache_key), so the store needs no
invalidation logic: a changed input produces a different key, and the stale
entry is simply never asked for again. Old entries are trimmed on save.

For today's pure-Python evaluator the cache is a modest win — the graph would
have to be enormous before recomputation hurt. It exists because the cache key
is the part that has to be right *now*: when a gate is later evaluated by a
model call instead of an expression, the key already isolates exactly the
inputs that could change the answer, and only those nodes pay for a re-run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CACHE_VERSION = 1
MAX_ENTRIES = 4000


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    def as_dict(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "writes": self.writes}


@dataclass
class Cache:
    """A dict of key -> derived record, optionally backed by a JSON file."""

    path: Path | None = None
    entries: dict[str, dict] = field(default_factory=dict)
    stats: CacheStats = field(default_factory=CacheStats)
    _touched: set[str] = field(default_factory=set)
    _dirty: bool = False

    @classmethod
    def load(cls, path: str | Path | None) -> Cache:
        if path is None:
            return cls()
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # A corrupt cache is never fatal: it is derived data by definition.
            return cls(path=path)
        if payload.get("version") != CACHE_VERSION:
            return cls(path=path)
        return cls(path=path, entries=payload.get("entries") or {})

    def get(self, key: str) -> dict | None:
        hit = self.entries.get(key)
        if hit is None:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        self._touched.add(key)
        return hit

    def put(self, key: str, value: dict) -> None:
        self.entries[key] = value
        self._touched.add(key)
        self.stats.writes += 1
        self._dirty = True

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        entries = self.entries
        if len(entries) > MAX_ENTRIES:
            # Keep everything this run touched, then backfill with the most
            # recently inserted older entries until the cap is reached.
            keep = {k: entries[k] for k in self._touched if k in entries}
            for key in reversed(list(entries)):
                if len(keep) >= MAX_ENTRIES:
                    break
                keep.setdefault(key, entries[key])
            entries = keep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "entries": entries}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(self.path)
        self._dirty = False
