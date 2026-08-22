"""`--json` is an interface, not presentation.

The human CLI is one client. Everything else — renderers, corroborators, a
future desktop or dashboard — reads the JSON, and a key that quietly changes
name or disappears breaks all of them at once with no error anywhere.

So the payload *shape* is pinned here. Values are free to change: that is the
tool doing its job. Keys are the contract.

If a test in this file fails, the question is not "how do I make it pass". It
is **"am I changing an interface on purpose?"** If yes, edit the expected shape
in the same commit, and the diff is the record that it was deliberate. This
exists because a pass over the terminal output is exactly the kind of work that
reformats a payload by accident.
"""

import json

import pytest

from trellis import cli

GRAPH = """nodes:
  - id: contract.handoff
    title: The handoff
    kind: contract
    status: agreed
    version: 1
    satisfied_by: [api.schema]
  - id: api
    title: API
    status: in_progress
    publishes:
      schema_ready: has(api.schema, "schema")
  - id: api.schema
    title: Schema work
    parent: api
    status: done
    provides: [schema]
  - id: consumer
    title: Consumer
    status: not_started
    gates:
      start: api.schema.done and contract.handoff.live
    evidence:
      api.schema: {how: verified, at: 2026-08-01}
      contract.handoff: inferred
    notes: A node with most of the optional fields set.
"""


@pytest.fixture
def workspace(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(GRAPH)
    return graph_dir


def shape(payload):
    """The structure of a payload, with every value replaced by its type.

    Lists collapse to the shape of their first element: a consumer indexes
    into a list, it does not depend on how many entries there happen to be.
    """
    if isinstance(payload, dict):
        return {key: shape(value) for key, value in sorted(payload.items())}
    if isinstance(payload, list):
        return [shape(payload[0])] if payload else []
    if payload is None:
        return "null"
    return type(payload).__name__


def payload_of(workspace, capsys, *args):
    cli.main(["--graph", str(workspace), "--json", *args])
    return json.loads(capsys.readouterr().out)


# The pinned interface. Each entry is a command and the keys a consumer can
# rely on. `null` appears where a field is legitimately optional in this
# fixture; a consumer must handle it either way, so it is part of the contract.
EXPECTED_KEYS = {
    ("check",): {"node", "code", "severity", "message"},
    ("ready",): {
        "id",
        "kind",
        "status",
        "readiness",
        "ref",
        "gates",
        "exports",
        "exports_hash",
        "violations",
    },
    ("explain", "consumer"): {"node", "readiness", "reasons"},
    ("impact", "api.schema"): {
        "overlay",
        "changes",
        "created",
        "unlocked",
        "newly_blocked",
        "contracts_lit",
        "contracts_dark",
        "violations_introduced",
        "violations_cleared",
        "cost",
    },
    ("deps", "consumer"): {"node", "direction", "nodes"},
    ("state",): {"summary", "nodes"},
    ("trust",): {
        "stale",
        "churning",
        "unknown",
        "unconfirmed_edges",
        "stale_verifications",
        "edge_coverage",
        "calibration",
        "corrections",
        "stale_proposals",
    },
}


@pytest.mark.parametrize("command", sorted(EXPECTED_KEYS), ids=lambda c: c[0])
def test_the_keys_a_consumer_indexes_are_pinned(workspace, capsys, command):
    payload = payload_of(workspace, capsys, *command)
    rows = payload if isinstance(payload, list) else [payload]
    assert rows, f"{command} returned nothing to check"
    for row in rows:
        missing = EXPECTED_KEYS[command] - set(row)
        assert not missing, (
            f"`trellis {' '.join(command)} --json` no longer emits {sorted(missing)}. "
            f"If that is deliberate, change EXPECTED_KEYS in the same commit - the "
            f"diff is the record that an interface change was intended."
        )


def test_trust_reports_calibration_as_counts_not_a_rate(workspace, capsys):
    """The shape carries a promise as well as a schema.

    Handing a consumer a rate would be the tool drawing the one conclusion the
    rest of its output refuses to draw, so the absence of one is pinned too.
    """
    payload = payload_of(workspace, capsys, "trust")
    calibration = payload["calibration"]
    assert {"checked", "wrong", "by_how", "by_source", "last_checked"} <= set(
        calibration
    )
    for key in calibration:
        assert "rate" not in key and "percent" not in key


def test_every_json_command_emits_valid_json(workspace, capsys):
    """A command that prints prose under `--json` is broken for every consumer.

    Cheap to get wrong: `--json` is a global flag, so a new command inherits it
    whether or not anyone remembered to honour it.
    """
    commands = [
        ["check"],
        ["state"],
        ["ready"],
        ["trust"],
        ["drift"],
        ["history"],
        ["explain", "consumer"],
        ["impact", "api.schema"],
        ["deps", "consumer"],
        ["blocking", "--all"],
        ["pending"],
        ["stats"],
    ]
    for command in commands:
        cli.main(["--graph", str(workspace), "--json", *command])
        out = capsys.readouterr().out
        try:
            json.loads(out)
        except json.JSONDecodeError as exc:
            pytest.fail(f"`trellis {' '.join(command)} --json` emitted non-JSON: {exc}")


def test_a_node_row_is_shaped_the_same_everywhere(workspace, capsys):
    """`state` and `ready` describe the same thing and must agree.

    Two spellings of one row is the kind of drift a consumer finds in
    production, and they are built by different call sites.
    """
    # `state` keys its nodes by id; `ready` returns a list of the same rows.
    state = {
        node_id: shape(row)
        for node_id, row in payload_of(workspace, capsys, "state")["nodes"].items()
    }
    for row in payload_of(workspace, capsys, "ready"):
        assert shape(row) == state[row["id"]], (
            f"`ready` and `state` disagree about the shape of {row['id']}"
        )
