"""Checking the declaration against systems trellis does not own.

A corroborator answers one question: does what the graph says still match what
some system of record says? It is handed a snapshot and returns findings. It
**cannot set anything** — not a status, not a gate, not an edge. That is the
same rule the rest of the trust layer follows, applied across a process
boundary where it becomes structural rather than a promise.

Four constraints, each earned rather than chosen:

**External findings are never errors.** `error` in this project means the graph
cannot be evaluated — a cycle, a dangling reference. Only the kernel can know
that. A corroborator disagreeing with Linear is a question, however confident
it is, so `info` and `warn` are the whole range available to it. That also
keeps `doctor`'s ranking honest: external findings slot into known bands rather
than competing with facts the kernel established itself.

**Codes are namespaced.** `linear:state_disagrees`, not `state_disagrees`. A
reader can see where a finding came from, and a corroborator cannot
accidentally impersonate a kernel diagnostic.

**Failing is a finding, not a crash — and never a silence.** A corroborator that
could not run has not told you the graph is fine; it has told you nothing. That
distinction has already cost this project twice, so a failure is reported as
loudly as a disagreement.

**A clean result says what it compared.** `26 rows checked, no conflicts` is not
a result; `26 rows, status only - relations unchecked` is. Reporting clean
without naming the scope hides a whole missing category behind a true number:
a checker that compared only attributes once reported no conflicts across 26
rows while an edge the record had held since the ticket was written was absent
from the graph. Same failure as the one above, arriving as a number rather than
as an exception.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .loader import project_root

CONFIG_NAME = "trellis.toml"
# What an external source may claim. `error` is reserved for the kernel.
ALLOWED_SEVERITY = ("info", "warn")
DEFAULT_TIMEOUT = 30
# Not every finding is about a node. "33 tickets in the tables have no node" is
# a fact about the whole tree, and it rendered as an empty column and a stray
# colon. The kernel already labels its own graph-level findings this way.
GRAPH_LEVEL = "(graph)"


class CorroboratorError(RuntimeError):
    """A corroborator is misconfigured. Distinct from one that ran and failed."""


@dataclass
class Finding:
    node: str
    code: str
    severity: str
    message: str
    source: str

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
        }


def load(graph_dir: str | Path) -> dict[str, dict]:
    """Corroborators declared in trellis.toml, if there is one."""
    path = project_root(graph_dir) / CONFIG_NAME
    if not path.exists():
        return {}
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise CorroboratorError(f"{CONFIG_NAME}: {exc}") from exc

    declared = config.get("corroborator") or {}
    if not isinstance(declared, dict):
        raise CorroboratorError(f"{CONFIG_NAME}: [corroborator.<name>] tables expected")
    for name, spec in declared.items():
        command = spec.get("command")
        if not isinstance(command, list) or not command:
            raise CorroboratorError(
                f'{CONFIG_NAME}: corroborator {name!r} needs command = ["prog", ...]'
            )
    return declared


def _parse(name: str, raw: bytes) -> list[Finding]:
    """Read what a corroborator returned, refusing anything it may not claim."""
    try:
        payload = json.loads(raw or b"[]")
    except json.JSONDecodeError as exc:
        raise CorroboratorError(f"{name}: output is not JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise CorroboratorError(f"{name}: expected a list of findings")

    out: list[Finding] = []
    for item in payload:
        if not isinstance(item, dict):
            raise CorroboratorError(f"{name}: each finding must be an object")
        severity = str(item.get("severity", "info"))
        if severity not in ALLOWED_SEVERITY:
            raise CorroboratorError(
                f"{name}: severity {severity!r} is not allowed - a corroborator "
                f"may report {' or '.join(ALLOWED_SEVERITY)}, never error. "
                f"An error means the graph cannot be evaluated, which only the "
                f"kernel can establish."
            )
        code = str(item.get("code") or "finding")
        out.append(
            Finding(
                node=str(item.get("node") or "").strip() or GRAPH_LEVEL,
                # Namespaced so a reader can see where it came from, and so a
                # corroborator cannot impersonate a kernel diagnostic.
                code=f"{name}:{code}",
                severity=severity,
                message=str(item.get("message", "")).strip(),
                source=name,
            )
        )
    return out


def run(
    name: str, spec: dict, payload: dict, timeout: int = DEFAULT_TIMEOUT
) -> list[Finding]:
    """Run one corroborator. Failure to run is itself reported as a finding."""
    try:
        result = subprocess.run(
            spec["command"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return [_could_not_run(name, f"{spec['command'][0]!r} not found")]
    except subprocess.TimeoutExpired:
        return [_could_not_run(name, f"timed out after {timeout}s")]
    except (OSError, subprocess.SubprocessError) as exc:
        return [_could_not_run(name, str(exc))]

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or "no output"
        return [_could_not_run(name, f"exited {result.returncode}: {detail}")]

    try:
        return _parse(name, result.stdout)
    except CorroboratorError as exc:
        return [_could_not_run(name, str(exc))]


def _could_not_run(name: str, detail: str) -> Finding:
    """A corroborator that did not run has told you nothing, not that all is well."""
    # The detail is often another diagnostic, which ends in a full stop of its
    # own: "only the kernel can establish.. This is silence, not agreement".
    detail = detail.strip().removesuffix(".")
    return Finding(
        node=GRAPH_LEVEL,
        code=f"{name}:did_not_run",
        severity="warn",
        message=(
            f"could not be checked against {name}: {detail}. "
            f"This is silence, not agreement - whatever it would have found is "
            f"unknown."
        ),
        source=name,
    )


def gather(
    graph_dir: str | Path, payload: dict, timeout: int = DEFAULT_TIMEOUT
) -> list[Finding]:
    """Every declared corroborator's findings, in a stable order."""
    out: list[Finding] = []
    for name, spec in sorted(load(graph_dir).items()):
        out.extend(run(name, spec, payload, timeout))
    return out
