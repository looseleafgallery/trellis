"""Terminal presentation: colour, weight, and the box-drawing fallback.

Presentation only. Nothing here may change what a command *says* — every
mark, code, readiness word and remedy string survives with colour off, and
the plain output is the coloured output minus the escapes. That is the
property the tests in tests/test_style.py hold, and it is what lets this be
a preference (`--ascii`, `NO_COLOR`) without a preference ever reaching a
consumer interface: `--json` and the renderer contract never call in here.

The palette is four colours and a rule. Colour carries meaning or it does not
go in, so amber means exactly one thing — a person is owed a decision — and
appears nowhere else.
"""

from __future__ import annotations

import os
import sys

# 256-colour indices. Three already exist in the mermaid classDefs; keeping
# the same values means the terminal and the rendered flowchart agree about
# what blocked looks like.
AMBER = 214  # a person is owed a decision — and nothing else, ever
RED = 167  # blocked, or a defect
GREEN = 107  # ready, verified, held
DIM = 242  # settled, scaffolding, metadata
FAINT = 238  # deep scaffolding: tree branches, finding codes

_RESET = "\033[0m"

# Readiness word -> colour. Anything absent stays uncoloured rather than
# defaulting, because a wrong colour reads as a claim and no colour does not.
_READINESS = {
    "blocked": RED,
    "awaiting": AMBER,
    "ready": GREEN,
    "live": GREEN,
    "done": GREEN,
    "unverified": AMBER,
    "active": None,
    "pending": None,
    "unagreed": DIM,
    "draft": DIM,
    "superseded": DIM,
    "abandoned": DIM,
}

_SEVERITY = {"error": RED, "warn": AMBER, "info": DIM}

# Deliberately disjoint from viz.MARKS. A reader must never have to work out
# whether a glyph is telling them about severity or about readiness.
SEVERITY_GLYPHS = {"error": "!", "warn": "▲", "info": "i"}


class Style:
    """Whether to paint, and how. One instance per command invocation.

    Passed explicitly rather than read from a global, so a test can construct
    a painting Style without a tty and a plain one without unsetting the
    environment.
    """

    def __init__(self, *, colour: bool, unicode: bool) -> None:
        self.colour = colour
        self.unicode = unicode

    @classmethod
    def detect(cls, args=None, stream=None) -> Style:
        """The real decision, from the environment and the flags.

        Colour is dropped entirely when stdout is not a tty, when NO_COLOR is
        set to anything at all, or under --json. A pipe gets the same bytes a
        pipe has always got.
        """
        stream = stream or sys.stdout
        as_json = bool(getattr(args, "json", False))
        no_colour_env = os.environ.get("NO_COLOR") is not None
        tty = bool(getattr(stream, "isatty", lambda: False)())
        colour = tty and not no_colour_env and not as_json

        # --ascii is the opt-out, so box-drawing is what you get by default;
        # but a terminal that cannot encode the characters would print
        # mojibake, which is worse than the ASCII it replaced.
        encoding = (getattr(stream, "encoding", None) or "").lower()
        encodable = "utf" in encoding
        unicode_ok = encodable and not bool(getattr(args, "ascii", False))
        return cls(colour=colour, unicode=unicode_ok)

    # -- painting ---------------------------------------------------------

    def paint(self, text: str, colour: int | None, *, bold: bool = False) -> str:
        if not self.colour or colour is None or not text:
            return text
        weight = "1;" if bold else ""
        return f"\033[{weight}38;5;{colour}m{text}{_RESET}"

    def decision(self, text: str) -> str:
        """Amber. Only for a place where a person is owed a decision."""
        return self.paint(text, AMBER)

    def blocked(self, text: str) -> str:
        return self.paint(text, RED)

    def ready(self, text: str) -> str:
        return self.paint(text, GREEN)

    def dim(self, text: str) -> str:
        return self.paint(text, DIM)

    def scaffold(self, text: str) -> str:
        """Tree branches and finding codes: present, greppable, out of the way."""
        return self.paint(text, FAINT)

    def mark(self, glyph: str, readiness: str) -> str:
        """A readiness mark from viz.MARKS. Bold — marks carry the scan path."""
        return self.paint(glyph, _READINESS.get(readiness), bold=True)

    def readiness(self, word: str) -> str:
        return self.paint(word, _READINESS.get(word))

    def severity(self, text: str, severity: str) -> str:
        return self.paint(text, _SEVERITY.get(severity))

    def glyph(self, severity: str) -> str:
        """The gutter glyph for a severity block. Bold, like a mark."""
        return self.paint(
            SEVERITY_GLYPHS.get(severity, "-"), _SEVERITY.get(severity), bold=True
        )

    # -- box drawing ------------------------------------------------------

    @property
    def branch_last(self) -> str:
        return "└─ " if self.unicode else "`- "

    @property
    def branch_mid(self) -> str:
        return "├─ " if self.unicode else "|- "

    @property
    def branch_pipe(self) -> str:
        return "│  " if self.unicode else "|  "

    @property
    def rule(self) -> str:
        """The block rule used where a fraction would have to be read."""
        return "━" if self.unicode else "="


PLAIN = Style(colour=False, unicode=False)
