"""The degradation contract: colour is presentation and nothing else.

These hold the one property the whole style layer rests on — that turning
colour off removes escape sequences and changes nothing a reader or a
consumer depends on. If any of these fail, a preference has reached
something it should never touch.
"""

from __future__ import annotations

import re

import pytest

from trellis import viz
from trellis.engine import Engine
from trellis.style import PLAIN, SEVERITY_GLYPHS, Style

ANSI = re.compile(r"\033\[[0-9;]*m")


def strip(text: str) -> str:
    return ANSI.sub("", text)


class FakeStream:
    def __init__(self, *, tty: bool, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


class Args:
    def __init__(self, *, json: bool = False, ascii: bool = False) -> None:
        self.json = json
        self.ascii = ascii


# -- when colour is allowed at all -------------------------------------------


def test_a_pipe_gets_no_colour():
    st = Style.detect(Args(), FakeStream(tty=False))
    assert st.colour is False


def test_no_color_env_wins_over_a_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "")
    st = Style.detect(Args(), FakeStream(tty=True))
    assert st.colour is False, "NO_COLOR set to empty string must still disable"


def test_json_is_never_coloured_even_on_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    st = Style.detect(Args(json=True), FakeStream(tty=True))
    assert st.colour is False


def test_a_tty_without_no_color_paints(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    st = Style.detect(Args(), FakeStream(tty=True))
    assert st.colour is True


# -- box drawing --------------------------------------------------------------


def test_ascii_flag_restores_the_old_branches(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    st = Style.detect(Args(ascii=True), FakeStream(tty=True))
    assert st.branch_last == "`- "
    assert st.branch_mid == "|- "
    assert st.branch_pipe == "|  "


def test_a_non_utf8_terminal_falls_back_without_being_asked(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    st = Style.detect(Args(), FakeStream(tty=True, encoding="ascii"))
    assert st.unicode is False, "mojibake is worse than the ASCII it replaced"


def test_box_drawing_does_not_depend_on_a_tty(monkeypatch):
    """Colour is about the terminal; branch characters are about encoding."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    st = Style.detect(Args(), FakeStream(tty=False))
    assert st.colour is False
    assert st.unicode is True


# -- the contract itself ------------------------------------------------------


def test_severity_glyphs_never_collide_with_readiness_marks():
    """A reader must never have to ask which scale a glyph belongs to."""
    assert not (set(SEVERITY_GLYPHS.values()) & set(viz.MARKS.values()))


def test_painting_only_adds_escapes():
    st = Style(colour=True, unicode=True)
    for text in ("blocked", "x", "sys.b", "dangling_reference"):
        assert strip(st.decision(text)) == text
        assert strip(st.blocked(text)) == text
        assert strip(st.scaffold(text)) == text
        assert strip(st.mark(text, "blocked")) == text


def test_plain_style_emits_no_escapes_at_all():
    assert "\033" not in PLAIN.decision("a person is owed a decision")
    assert PLAIN.paint("x", 214, bold=True) == "x"


@pytest.fixture
def engine():
    from tests.test_trellis import EXAMPLE
    from trellis.loader import load_graph

    return Engine(load_graph(EXAMPLE))


def test_the_tree_says_the_same_thing_painted_or_not(engine):
    nodes = set(engine.graph.ids())
    painted = viz.tree(engine, nodes, st=Style(colour=True, unicode=True))
    plain = viz.tree(engine, nodes, st=Style(colour=False, unicode=True))
    assert strip(painted) == plain


def test_columns_survive_painting(engine):
    """Widths are measured unpainted; counting escapes would break every row."""
    nodes = set(engine.graph.ids())
    painted = viz.tree(engine, nodes, st=Style(colour=True, unicode=True))
    plain = viz.tree(engine, nodes, st=Style(colour=False, unicode=True))
    for painted_row, plain_row in zip(
        painted.splitlines(), plain.splitlines(), strict=True
    ):
        assert strip(painted_row) == plain_row


def test_every_node_still_appears_with_colour_off(engine):
    nodes = set(engine.graph.ids())
    plain = viz.tree(engine, nodes, st=PLAIN)
    for node_id in nodes:
        assert node_id in plain


def test_the_slice_waits_on_its_unsettled_leaves(engine):
    nodes = set(engine.graph.ids())
    leaves = viz.slice_leaves(engine, nodes)
    derived = engine.all_derived()
    for node_id in leaves:
        assert not any(t in nodes for t in engine.graph.references_of(node_id))
        assert derived[node_id].readiness not in {"done", "live"}
