"""Who may change an agreed contract.

The working split this exists for: agents implement inside contracts, and a
person decides when a contract changes. That only holds if the loop can tell
the two apart, and the one thing it can see is whether someone is at a
terminal. `--yes`, `--because`, `--propose` and `accept` are all available to
an agent, so none of them is a person; an interactive stdin is not proof of
one either, but reaching it is a deliberate act rather than drift.

So a change to a contract that is `agreed` or `frozen` - moving its version,
or walking its status back to `draft` or `proposed` - needs a terminal and a
reason. Queuing it with `--propose` is never refused: that is the route an
agent takes, and the person accepting it is the one the check applies to.

`trellis.toml` can loosen it:

    [loop]
    agreed_contracts = "because"    # a reason is enough; the default is "tty"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .loader import project_root
from .model import Graph

CONFIG_NAME = "trellis.toml"
MODES = ("tty", "because")
DEFAULT_MODE = "tty"
# Most guarded first: where the file and the journal disagree, the stricter
# reading is the one that counts.
GUARDED_STATUSES = ("frozen", "agreed")
# Walking back to one of these is the change. `agreed -> frozen` moves forward.
WALKED_BACK_TO = ("draft", "proposed")


class GuardError(ValueError):
    """`[loop] agreed_contracts` is set to something that is not a mode."""


def mode(graph_dir: str | Path) -> str:
    """The configured mode, or the default when nothing configures one."""
    path = project_root(graph_dir) / CONFIG_NAME
    if not path.exists():
        return DEFAULT_MODE
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise GuardError(f"{CONFIG_NAME}: {exc}") from exc
    value = (config.get("loop") or {}).get("agreed_contracts", DEFAULT_MODE)
    if value not in MODES:
        raise GuardError(
            f"{CONFIG_NAME}: [loop] agreed_contracts = {value!r} is not a mode - "
            f"use {' or '.join(repr(m) for m in MODES)}"
        )
    return value


@dataclass(frozen=True)
class Guarded:
    """One change to an agreed or frozen contract."""

    node: str
    status: str
    what: str

    def refusal(self) -> str:
        """What a change made without a terminal is told. Pinned by a test."""
        return (
            f"refused: {self.node} is {self.status}, and this changes it "
            f"({self.what}). A person has to do this at a terminal, or queue it "
            f"with --propose for one to accept. To allow it without a terminal, "
            f'set [loop] agreed_contracts = "because" in {CONFIG_NAME}.'
        )

    def needs_reason(self) -> str:
        return (
            f"refused: {self.node} is {self.status}, and this changes it "
            f"({self.what}). --because is required."
        )


def _counted_status(file_status: object, journaled_status: object) -> str | None:
    """The guarded status this contract counts as, if either source says one.

    The file alone is not enough: hand-editing `agreed` to `draft`, making the
    change, and hand-editing it back would leave no drift and no person
    involved. A status trellis wrote is one a hand edit has not yet answered for.
    """
    for status in GUARDED_STATUSES:
        if status in (file_status, journaled_status):
            return status
    return None


def _shown(value: object) -> str:
    return "none" if value is None or value == "" else str(value)


def guarded(
    graph: Graph,
    journaled_status: dict[str, object],
    changes: list[tuple[str, str, object, object]],
) -> list[Guarded]:
    """Which of these `(node, field, before, after)` changes the guard covers.

    Every route into the graph passes its changes here in the same shape - a
    write, an accepted proposal, an accepted drift - so accepting a change is
    never looser or stricter than making it directly.
    """
    out: list[Guarded] = []
    for node_id, field, before, after in changes:
        if node_id not in graph or before == after:
            continue
        node = graph.get(node_id)
        if node.kind != "contract":
            continue
        status = _counted_status(node.status, journaled_status.get(node_id))
        if status is None:
            continue
        if field == "version":
            out.append(
                Guarded(node_id, status, f"version {_shown(before)} -> {_shown(after)}")
            )
        elif field == "status" and after in WALKED_BACK_TO:
            out.append(
                Guarded(node_id, status, f"status {_shown(before)} -> {_shown(after)}")
            )
    return out
