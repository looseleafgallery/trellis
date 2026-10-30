"""Who may change an agreed contract (#100, G1).

Agents implement inside contracts; a person decides when one changes. Moving
an agreed or frozen contract's version, or walking its status back, needs a
terminal and a reason unless `trellis.toml` loosens it - through every route
that can make the change, so accepting it is never a way around making it.
"""

import json
import sys
from pathlib import Path

import pytest
from test_drift_fields import hand_set

from trellis import cli, journal, proposals
from trellis.loader import load_graph, project_root

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "agent-loop" / "graph"

TAIL = (
    "A person has to do this at a terminal, or queue it with --propose for one "
    "to accept. To allow it without a terminal, set [loop] agreed_contracts = "
    '"because" in trellis.toml.'
)


def refusal(node: str, status: str, what: str) -> str:
    return f"refused: {node} is {status}, and this changes it ({what}). {TAIL}"


@pytest.fixture
def workspace(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for src in EXAMPLE.glob("*.yaml"):
        (graph_dir / src.name).write_text(src.read_text())
    return graph_dir


@pytest.fixture
def terminal(monkeypatch):
    """Pretend a person is at a terminal, answering prompts in order."""

    def at(*responses):
        queue = list(responses)

        def fake_input(_prompt=""):
            if not queue:
                raise EOFError
            return queue.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    return at


@pytest.fixture
def no_terminal(monkeypatch):
    def leave():
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    return leave


def configure(graph_dir, mode: str) -> None:
    (project_root(graph_dir) / "trellis.toml").write_text(
        f'[loop]\nagreed_contracts = "{mode}"\n'
    )


def trellis(graph_dir, *argv) -> int:
    return cli.main(["--graph", str(graph_dir), *argv])


def contract(graph_dir, node="contract.tool_schema"):
    return load_graph(graph_dir).get(node)


def refused_cleanly(graph_dir, capsys, code, expected, entries=0):
    err = capsys.readouterr().err
    assert code != 0
    assert expected in err.splitlines()
    assert len(journal.read(graph_dir)) == entries
    return err


# -- what is guarded -----------------------------------------------------------


def test_moving_an_agreed_contracts_version_needs_a_terminal(workspace, capsys):
    code = trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.tool_schema", "agreed", "version 2 -> 3"),
    )
    assert contract(workspace).version == 2


@pytest.mark.parametrize("to", ["draft", "proposed"])
def test_walking_an_agreed_contract_back_needs_a_terminal(workspace, capsys, to):
    code = trellis(
        workspace, "set", "contract.tool_schema", f"status={to}", "-y", "--because", "x"
    )
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.tool_schema", "agreed", f"status agreed -> {to}"),
    )
    assert contract(workspace).status == "agreed"


def test_freezing_an_agreed_contract_moves_forward_and_is_not_guarded(workspace):
    assert trellis(workspace, "set", "contract.tool_schema", "status=frozen", "-y") == 0
    assert contract(workspace).status == "frozen"


def test_a_frozen_contract_is_guarded_as_frozen(workspace, capsys):
    trellis(workspace, "set", "contract.tool_schema", "status=frozen", "-y")
    entries = len(journal.read(workspace))
    capsys.readouterr()

    code = trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.tool_schema", "frozen", "version 2 -> 3"),
        entries,
    )


def test_fields_that_do_not_change_what_it_means_are_not_guarded(workspace):
    assert (
        trellis(workspace, "set", "contract.tool_schema", "title=Tools v2", "-y") == 0
    )


def test_a_contract_that_is_not_agreed_is_not_guarded(workspace):
    assert trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y") == 0


def test_the_refusal_never_offers_a_way_around(workspace, capsys):
    trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    err = capsys.readouterr().err.lower()
    assert "bump" not in err
    assert "status=" not in err


# -- at a terminal -------------------------------------------------------------


def test_at_a_terminal_with_a_reason_it_goes_through(workspace, terminal):
    terminal()
    code = trellis(
        workspace, "set", "contract.tool_schema", "version=3", "-y", "--because", "v3"
    )
    assert code == 0
    assert contract(workspace).version == 3
    assert journal.read(workspace)[-1]["reason"] == "v3"


def test_at_a_terminal_without_a_reason_it_asks(workspace, terminal):
    terminal("the handoff envelope gained a field")
    assert trellis(workspace, "set", "contract.tool_schema", "version=3", "-y") == 0
    assert (
        journal.read(workspace)[-1]["reason"] == "the handoff envelope gained a field"
    )


def test_at_a_terminal_a_blank_reason_is_refused(workspace, terminal, capsys):
    terminal("")
    code = trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    assert code != 0
    assert "--because is required" in capsys.readouterr().err
    assert journal.read(workspace) == []


# -- the setting -----------------------------------------------------------------


def test_because_mode_needs_only_a_reason(workspace):
    configure(workspace, "because")
    code = trellis(
        workspace, "set", "contract.tool_schema", "version=3", "-y", "--because", "v3"
    )
    assert code == 0
    assert contract(workspace).version == 3


def test_because_mode_still_needs_the_reason(workspace, capsys):
    configure(workspace, "because")
    code = trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    refused_cleanly(
        workspace,
        capsys,
        code,
        "refused: contract.tool_schema is agreed, and this changes it "
        "(version 2 -> 3). --because is required.",
    )


def test_an_unknown_mode_is_refused_naming_both(workspace, capsys):
    configure(workspace, "never")
    code = trellis(
        workspace, "set", "contract.tool_schema", "version=3", "-y", "--because", "x"
    )
    assert code != 0
    err = capsys.readouterr().err
    assert "'never' is not a mode" in err
    assert "'tty' or 'because'" in err
    assert journal.read(workspace) == []


# -- every route ---------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "because"])
def test_proposing_is_never_guarded(workspace, mode):
    if mode:
        configure(workspace, mode)
    code = trellis(
        workspace,
        "set",
        "contract.tool_schema",
        "version=3",
        "--propose",
        "--because",
        "tests green",
    )
    assert code == 0
    assert len(proposals.pending(workspace)) == 1
    assert contract(workspace).version == 2


def queued(workspace):
    trellis(
        workspace,
        "set",
        "contract.tool_schema",
        "version=3",
        "--propose",
        "--because",
        "tests green",
    )
    return proposals.pending(workspace)[0].id


def test_accepting_a_queued_change_is_guarded(workspace, capsys):
    proposal = queued(workspace)
    capsys.readouterr()

    code = trellis(workspace, "accept", proposal, "-y", "--because", "fine")
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.tool_schema", "agreed", "version 2 -> 3"),
    )
    assert [p.id for p in proposals.pending(workspace)] == [proposal]


def test_the_proposers_reason_does_not_stand_in_for_the_acceptors(
    workspace, terminal, capsys
):
    proposal = queued(workspace)
    terminal("")  # at a terminal, but gives no reason of their own
    code = trellis(workspace, "accept", proposal, "-y")
    assert code != 0
    assert "--because is required" in capsys.readouterr().err
    assert contract(workspace).version == 2


def test_a_person_accepting_at_a_terminal_applies_it(workspace, terminal):
    proposal = queued(workspace)
    terminal()
    assert trellis(workspace, "accept", proposal, "-y", "--because", "reviewed") == 0
    assert contract(workspace).version == 3
    assert journal.read(workspace)[-1]["reason"] == "reviewed"


# -- accepting drift is gated exactly like the direct write (A7) ---------------


def same_refusal_both_ways(workspace, capsys, field, by_hand, restore, expected):
    """Accept the hand edit, then make the same change directly: same answer."""
    hand_set(workspace, "contract.tool_schema", field, by_hand)
    entries = len(journal.read(workspace))
    capsys.readouterr()
    code = trellis(
        workspace,
        "drift",
        "--accept",
        f"contract.tool_schema.{field}",
        "--because",
        "on purpose",
    )
    via_accept = refused_cleanly(workspace, capsys, code, expected, entries)

    hand_set(workspace, "contract.tool_schema", field, restore)
    code = trellis(
        workspace,
        "set",
        "contract.tool_schema",
        f"{field}={by_hand}",
        "-y",
        "--because",
        "on purpose",
    )
    via_set = refused_cleanly(workspace, capsys, code, expected, entries)
    assert via_accept == via_set


def test_accepting_a_version_drift_is_gated_like_set_version(
    workspace, terminal, no_terminal, capsys
):
    terminal()
    trellis(
        workspace, "set", "contract.tool_schema", "version=3", "-y", "--because", "v3"
    )
    no_terminal()
    same_refusal_both_ways(
        workspace,
        capsys,
        "version",
        4,
        3,
        refusal("contract.tool_schema", "agreed", "version 3 -> 4"),
    )


def test_accepting_a_status_drift_is_gated_like_set_status(workspace, capsys):
    trellis(workspace, "set", "contract.tool_schema", "status=frozen", "-y")
    same_refusal_both_ways(
        workspace,
        capsys,
        "status",
        "draft",
        "frozen",
        refusal("contract.tool_schema", "frozen", "status frozen -> draft"),
    )


def test_accepting_an_unguarded_drift_is_allowed_like_set(workspace):
    trellis(workspace, "set", "contract.tool_schema", "title=Tools", "-y")
    hand_set(workspace, "contract.tool_schema", "title", "Tools, by hand")
    code = trellis(
        workspace,
        "drift",
        "--accept",
        "contract.tool_schema.title",
        "--because",
        "renamed",
    )
    assert code == 0


# -- which status counts (L9) --------------------------------------------------


def test_a_hand_walk_back_does_not_lower_the_guard(workspace, capsys):
    """Hand-edit agreed -> draft, change it, hand-edit it back: no drift would
    be left and no person involved. The status trellis wrote still counts."""
    trellis(workspace, "set", "contract.stage_handoff", "status=agreed", "-y")
    hand_set(workspace, "contract.stage_handoff", "status", "draft")
    entries = len(journal.read(workspace))
    capsys.readouterr()

    code = trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y")
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.stage_handoff", "agreed", "version 1 -> 2"),
        entries,
    )


def test_an_accepted_walk_back_does_lower_it(workspace, terminal, no_terminal):
    trellis(workspace, "set", "contract.stage_handoff", "status=agreed", "-y")
    hand_set(workspace, "contract.stage_handoff", "status", "draft")
    terminal()
    code = trellis(
        workspace,
        "drift",
        "--accept",
        "contract.stage_handoff.status",
        "--because",
        "the envelope is being renegotiated",
    )
    assert code == 0

    no_terminal()
    assert trellis(workspace, "set", "contract.stage_handoff", "version=2", "-y") == 0


# -- a baseline for contracts written by hand ----------------------------------


def doctor_warnings(graph_dir, capsys):
    capsys.readouterr()
    trellis(graph_dir, "--json", "doctor")
    return [
        r["node"]
        for r in json.loads(capsys.readouterr().out)
        if r["severity"] == "warn" and "trellis baseline" in r["message"]
    ]


def test_doctor_warns_about_agreed_contracts_with_no_baseline(workspace, capsys):
    assert doctor_warnings(workspace, capsys) == [
        "contract.tool_schema",
        "contract.trace_format",
    ]


def test_without_a_baseline_a_hand_round_trip_goes_unseen(workspace):
    """The gap baseline exists for: the file is all the guard has."""
    hand_set(workspace, "contract.tool_schema", "status", "draft")
    assert trellis(workspace, "set", "contract.tool_schema", "version=3", "-y") == 0


def test_baseline_records_status_and_changes_no_yaml(workspace, capsys):
    before = {p.name: p.read_bytes() for p in workspace.glob("*.yaml")}
    assert trellis(workspace, "baseline") == 0

    assert {p.name: p.read_bytes() for p in workspace.glob("*.yaml")} == before
    entry = journal.read(workspace)[-1]
    assert entry["origin"] == "baseline"
    assert [(w["node"], w["field"], w["after"]) for w in entry["writes"]] == [
        ("contract.tool_schema", "status", "agreed"),
        ("contract.trace_format", "status", "agreed"),
    ]
    assert journal.drift(workspace, load_graph(workspace)) == []
    assert doctor_warnings(workspace, capsys) == []


def test_baseline_twice_records_nothing_the_second_time(workspace, capsys):
    trellis(workspace, "baseline")
    entries = len(journal.read(workspace))
    capsys.readouterr()

    assert trellis(workspace, "baseline") == 0
    assert "nothing to record" in capsys.readouterr().out
    assert len(journal.read(workspace)) == entries


def test_after_a_baseline_a_hand_round_trip_is_refused(workspace, capsys):
    trellis(workspace, "baseline")
    hand_set(workspace, "contract.tool_schema", "status", "draft")
    entries = len(journal.read(workspace))
    capsys.readouterr()

    code = trellis(workspace, "set", "contract.tool_schema", "version=3", "-y")
    refused_cleanly(
        workspace,
        capsys,
        code,
        refusal("contract.tool_schema", "agreed", "version 2 -> 3"),
        entries,
    )


def test_a_baseline_taken_after_a_walk_back_does_not_record_it(workspace):
    """Only as honest as the moment it runs, and it says so by what it skips."""
    hand_set(workspace, "contract.tool_schema", "status", "draft")
    trellis(workspace, "baseline")
    recorded = [w["node"] for w in journal.read(workspace)[-1]["writes"]]
    assert recorded == ["contract.trace_format"]
