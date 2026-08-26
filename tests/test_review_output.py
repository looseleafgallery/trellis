"""`review` shows one decision, and never offers a key it cannot honour.

The load-bearing rule here is the absent key. A greyed-out `[a]` still reads
as available, and `acknowledge` is precisely the option an error must never
offer — an error means the graph cannot evaluate, and acknowledging one would
let a broken graph be made to look clean.
"""

from __future__ import annotations

import re

from trellis import cli
from trellis.model import Graph, node_from_dict
from trellis.queries import Problem
from trellis.style import PLAIN, Style

ANSI = re.compile(r"\033\[[0-9;]*m")


def strip(text: str) -> str:
    return ANSI.sub("", text)


def graph_with_n1() -> Graph:
    return Graph({"n1": node_from_dict({"id": "n1", "status": "not_started"})})


def keys_for(severity: str, code: str = "undrafted_contract", siblings: int = 0):
    problem = Problem(code=code, severity=severity, node="n1", message="m")
    return [c.key for c in cli._choices(graph_with_n1(), problem, siblings)]


def test_an_error_is_not_offered_acknowledge():
    """Absent, not greyed. The whole point of the rule."""
    keys = keys_for("error")
    assert "a" not in keys
    assert "A" not in keys
    assert "s" in keys and "q" in keys, "the rest of the menu still stands"


def test_a_warning_is_offered_acknowledge():
    assert "a" in keys_for("warn")


def test_ack_all_appears_only_with_siblings():
    assert "A" not in keys_for("warn", siblings=0)
    assert "A" in keys_for("warn", siblings=2)


def test_an_error_with_siblings_still_offers_neither():
    keys = keys_for("error", code="dangling_reference", siblings=3)
    assert "a" not in keys and "A" not in keys


def test_the_menu_paints_the_bracket_and_not_the_meaning():
    problem = Problem(
        code="undrafted_contract", severity="warn", node="n1", message="m"
    )
    choices = cli._choices(graph_with_n1(), problem, 0)
    painted = cli._full_menu(choices, Style(colour=True, unicode=True))
    plain = cli._full_menu(choices, PLAIN)
    assert strip(painted) == plain, "colour may not change a single character"
    assert "\033" in painted and "\033" not in plain


def test_every_offered_key_appears_in_the_menu():
    problem = Problem(
        code="undrafted_contract", severity="warn", node="n1", message="m"
    )
    choices = cli._choices(graph_with_n1(), problem, 2)
    menu = cli._full_menu(choices, PLAIN)
    for choice in choices:
        assert f"[{choice.key}]" in menu


# -- the progress rule --------------------------------------------------------


def test_progress_is_a_rule_and_still_carries_the_number():
    out = cli._progress(3, 6, PLAIN, width=10)
    assert "3 of 6" in out, "the number is what a person quotes when they stop"
    assert out.count(PLAIN.rule) == 5


def test_progress_fills_and_empties_at_the_ends():
    assert cli._progress(0, 4, PLAIN, width=8).count(PLAIN.rule) == 0
    assert cli._progress(4, 4, PLAIN, width=8).count(PLAIN.rule) == 8


def test_progress_says_nothing_rather_than_dividing_by_zero():
    assert cli._progress(0, 0, PLAIN) == ""


def test_progress_survives_colour_being_off():
    painted = cli._progress(2, 5, Style(colour=True, unicode=True), width=10)
    plain = cli._progress(2, 5, Style(colour=False, unicode=True), width=10)
    assert strip(painted) == plain


def test_the_rule_falls_back_to_ascii():
    assert Style(colour=False, unicode=False).rule == "="
    assert "=" in cli._progress(1, 2, Style(colour=False, unicode=False), width=4)
