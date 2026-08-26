"""`doctor` groups by severity, and orders by urgency inside each group.

The two are different scales and a flat list invited them to be read as one:
position in a long list looks like importance, when position inside a
severity only ever meant "fix this one first".
"""

from __future__ import annotations

import pytest

from trellis import cli
from trellis.queries import DEFAULT_URGENCY, URGENCY
from trellis.style import SEVERITY_GLYPHS
from trellis.viz import MARKS


def write(tmp_path, body: str):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "g.yaml").write_text(body)
    return graph_dir


TWO_URGENCIES = (
    "nodes:\n"
    "  - id: zzz\n    status: not_started\n    gates: {start: nope.done}\n"
    "  - id: aaa\n    status: not_started\n    gates: {start: alsomissing.done}\n"
)


def test_urgency_beats_alphabetical_inside_a_block(tmp_path, capsys):
    """The regression this rewrite fixes.

    Both nodes raise a dangling_reference (urgency 0) and a gate_error
    (urgency 1). Sorted by node, they interleave: aaa/dangling, aaa/gate,
    zzz/dangling, zzz/gate. Sorted by urgency, both dangling references come
    first — which is correct, because nothing else can be trusted until they
    are gone.
    """
    graph_dir = write(tmp_path, TWO_URGENCIES)
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out

    # The footer names the first finding by code, so search the blocks only.
    blocks = out.split("start with")[0]
    positions = [
        blocks.index("dangling_reference"),
        blocks.rindex("dangling_reference"),
        blocks.index("gate_error"),
        blocks.rindex("gate_error"),
    ]
    assert positions == sorted(positions), (
        "both dangling_reference findings must precede both gate_error findings"
    )
    assert URGENCY["dangling_reference"] < URGENCY["gate_error"]


def test_blocks_run_worst_first(tmp_path, capsys):
    graph_dir = write(tmp_path, TWO_URGENCIES)
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    assert "\nerror\n" in out
    assert "\ninfo\n" in out
    assert out.index("\nerror\n") < out.index("\ninfo\n")


def test_the_tally_counts_what_is_shown(tmp_path, capsys):
    graph_dir = write(tmp_path, TWO_URGENCIES)
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    head = out.splitlines()[0]
    shown = sum(
        1
        for line in out.splitlines()
        if line.startswith(f"  {SEVERITY_GLYPHS['error']} ")
    )
    assert f"{shown} error" in head


def test_codes_are_present_and_greppable(tmp_path, capsys):
    """Moved to the right edge, out of the reading path — not removed."""
    graph_dir = write(tmp_path, TWO_URGENCIES)
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    assert "dangling_reference" in out
    assert "gate_error" in out


def test_a_remedy_still_follows_its_finding(tmp_path, capsys):
    graph_dir = write(tmp_path, TWO_URGENCIES)
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    assert "names a node that does not exist" in out


def test_gutter_glyphs_never_collide_with_readiness_marks():
    """Severity and readiness must never share a symbol.

    `?` and `.` were the old warn and info glyphs, and both are still
    readiness marks — `awaiting`, and `unagreed`/`draft`. A reader had no way
    to tell which scale a glyph belonged to.
    """
    assert not (set(SEVERITY_GLYPHS.values()) & set(MARKS.values()))
    assert "?" in MARKS.values() and "?" not in SEVERITY_GLYPHS.values()
    assert "." in MARKS.values() and "." not in SEVERITY_GLYPHS.values()


def test_an_acknowledged_finding_is_counted_not_silently_dropped(tmp_path, capsys):
    """An acknowledgement you cannot see is indistinguishable from a bug."""
    graph_dir = write(
        tmp_path,
        "nodes:\n"
        "  - id: solo\n    status: not_started\n    acknowledge: [unowned_node]\n",
    )
    cli.main(["--graph", str(graph_dir), "doctor"])
    out = capsys.readouterr().out
    assert "acknowledged and not shown" in out


@pytest.mark.xfail(
    strict=True,
    reason="dead_acknowledgement and drift have remedies but no URGENCY rank, so "
    "they sort at DEFAULT_URGENCY by accident rather than by decision. Where "
    "they belong is a judgement about severity, not a rendering fix, so this "
    "records the gap rather than guessing at ranks.",
)
def test_every_finding_code_that_fires_has_an_urgency():
    """A code missing from URGENCY sorts at DEFAULT and nobody notices."""
    from trellis.cli import REMEDIES

    missing = sorted(c for c in REMEDIES if c not in URGENCY)
    assert not missing, (
        f"codes with a remedy but no urgency rank, so they sort at "
        f"DEFAULT_URGENCY={DEFAULT_URGENCY} by accident: {missing}"
    )
