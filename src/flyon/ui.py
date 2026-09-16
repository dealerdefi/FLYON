"""
Drawing in a terminal.

One place for the boxes, the bars and the colour, so every command looks like
part of the same instrument rather than five people's print statements.

Three rules hold everywhere:

**Colour carries meaning, never decoration.** Green is money made, red is money
lost, gold is something that wants you — a wallet left off the board, an
endpoint that would not answer, a call still open. Everything else is quiet.

**A bar is always to scale, and the scale is always shown.** A bar drawn against
an invisible maximum is a picture that flatters whichever row happens to be
first.

**Every width is measured on the visible text.** Colour codes are invisible to a
person and four characters wide to `len()`, and a frame drawn with `len()` comes
out ragged the moment anything is coloured.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

ANSI = re.compile(r"\033\[[0-9;]*m")

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

#: name → xterm-256. The same green the banner uses, so the two agree.
INK = {
    "green": 112, "bright": 155, "lime": 118, "dim": 65, "mute": 244,
    "gold": 221, "amber": 179, "red": 174, "rose": 131, "line": 236, "deep": 22,
}

RESET = "\033[0m"

BOX = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│", "lt": "├", "rt": "┤", "tt": "┬", "bt": "┴",
}

BLOCKS = " ▏▎▍▌▋▊▉█"
SPARKS = "▁▂▃▄▅▆▇█"


def width() -> int:
    """The terminal, clamped. Wider than this and tables stop being readable."""
    return max(64, min(shutil.get_terminal_size((96, 24)).columns, 108))


def c(text: str, ink: str = "", bold: bool = False) -> str:
    if not COLOR or not ink:
        return text
    return f"\033[38;5;{INK[ink]}m" + ("\033[1m" if bold else "") + text + RESET


def visible(text: str) -> int:
    return len(ANSI.sub("", text))


def pad(text: str, n: int, align: str = "<") -> str:
    """Pad to n **visible** characters, truncating with an ellipsis if needed."""
    have = visible(text)
    if have > n:
        plain = ANSI.sub("", text)
        return plain[: max(0, n - 1)] + "…"
    space = " " * (n - have)
    if align == ">":
        return space + text
    if align == "^":
        left = (n - have) // 2
        return " " * left + text + " " * (n - have - left)
    return text + space


# ── bars and sparks ──────────────────────────────────────────────────────────


def bar(value: float, scale: float, cells: int, ink: str = "green") -> str:
    """
    A bar `cells` wide, drawn to `scale`, in eighths of a character.

    The eighth-blocks matter: at ten cells a whole-block bar can only say ten
    things, and rounding a leaderboard to ten buckets makes rows look equal that
    are not.
    """
    if scale <= 0 or cells <= 0:
        return " " * max(0, cells)
    filled = max(0.0, min(1.0, value / scale)) * cells
    whole = int(filled)
    part = int((filled - whole) * 8)
    out = "█" * whole + (BLOCKS[part] if part and whole < cells else "")
    return c(out.ljust(cells), ink)


def diverging(value: float, scale: float, cells: int) -> str:
    """
    A bar that grows either way from a centre line — for profit and loss.

    Reading a mixed column of gains and losses as two separate left-aligned
    bars takes a second longer than it should; growing them apart from a shared
    zero takes none.
    """
    half = cells // 2
    if scale <= 0:
        return " " * cells
    n = min(half, int(round(abs(value) / scale * half)))
    if value >= 0:
        return " " * half + c("█" * n, "green") + " " * (half - n)
    return " " * (half - n) + c("█" * n, "rose") + " " * half


def spark(values: list[float], ink: str = "dim") -> str:
    """A row of eighths. Flat data draws a flat line rather than noise."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-15:
        return c(SPARKS[0] * len(values), ink)
    return c("".join(SPARKS[int((v - lo) / (hi - lo) * (len(SPARKS) - 1))] for v in values), ink)


def strip(outcomes: list[bool], cap: int = 20) -> str:
    """Wins and losses in the order they happened. The shape of a record."""
    marks = [c("▲", "green") if o else c("▾", "rose") for o in outcomes[-cap:]]
    return "".join(marks)


# ── frames ───────────────────────────────────────────────────────────────────


def inner(w: int | None = None) -> int:
    """The room inside a frame: two border characters and two of padding."""
    return (w or width()) - 4


def flex(total: int, fixed: list[int], gap: int = 2, floor: int = 6) -> int:
    """
    How wide the one stretchy column can be.

    Laying a table out against the terminal width and then putting a frame
    around it is how a table ends up one character too wide and every row ends
    in an ellipsis. The frame is subtracted first, here, once.
    """
    used = sum(fixed) + gap * len(fixed)
    return max(floor, total - used)


def rule(n: int, ink: str = "line") -> str:
    return c(BOX["h"] * n, ink)


def head(title: str, note: str = "", w: int | None = None) -> list[str]:
    """
    A section heading with its own rule, and a right-hand note.

        ── TOP BY REALISED PROFIT ──────────────── 25 of 35 wallets ──
    """
    w = w or width()
    left = f"{BOX['h']}{BOX['h']} {c(title.upper(), 'bright', bold=True)} "
    right = f" {c(note, 'mute')} {BOX['h']}{BOX['h']}" if note else BOX["h"] * 2
    fill = max(1, w - visible(left) - visible(right))
    return ["", left + c(BOX["h"] * fill, "line") + right]


def frame(rows: list[str], w: int | None = None, ink: str = "line",
          title: str = "", foot: str = "") -> list[str]:
    """A box around a block of lines, with an optional caption on each edge."""
    w = w or width()
    inner = w - 4

    top = BOX["tl"] + BOX["h"] * (w - 2) + BOX["tr"]
    if title:
        label = f" {title} "
        top = (BOX["tl"] + BOX["h"] * 2 + label
               + BOX["h"] * max(0, w - 4 - len(label)) + BOX["tr"])
    bottom = BOX["bl"] + BOX["h"] * (w - 2) + BOX["br"]
    if foot:
        label = f" {foot} "
        bottom = (BOX["bl"] + BOX["h"] * 2 + label
                  + BOX["h"] * max(0, w - 4 - len(label)) + BOX["br"])

    out = [c(top, ink)]
    for row in rows:
        out.append(c(BOX["v"], ink) + " " + pad(row, inner) + " " + c(BOX["v"], ink))
    out.append(c(bottom, ink))
    return out


def columns(rows: list[list[str]], spec: list[tuple[int, str]], gap: int = 2) -> list[str]:
    """Lay rows out in fixed columns of (width, alignment)."""
    sep = " " * gap
    return [sep.join(pad(cell, w, a) for cell, (w, a) in zip(row, spec)) for row in rows]


def kv(label: str, value: str, note: str = "", label_w: int = 12) -> str:
    line = c(pad(label, label_w), "mute") + value
    return line + ("   " + c(note, "mute") if note else "")
