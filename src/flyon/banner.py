"""
The mark, for a terminal.

The wordmark is a 5x7 pixel alphabet drawn in this file rather than taken from a
font, so it renders identically on every machine and can be read and changed
without a design tool. The fly above it is the project's artwork, reduced once
to a grid of intensities and pasted here — reduced offline, so nothing at
runtime needs an image library.

Five levels, 0 to 4, coloured from the same green the rest of the terminal uses.
Two pixel rows share one terminal row through the half-block character, which is
what keeps the fly from looking like a smudge.

    from flyon.banner import render
    print("\n".join(render()))

`NO_COLOR` and a pipe both drop the colour and keep the shape.
"""

from __future__ import annotations

#: The fly: 28 columns, 18 pixel rows → 9 terminal rows,
#: trimmed of the empty margin so it centres on its own ink.
FLY = """\
0000000000000000000011333331
0000000000000001112222121113
0000000000010111222222222113
0000000002122222222221111221
0000001211121212121212121100
1233324223332232221101100000
2333343433433444433210000000
3333333333333333333343200000
2333333333333333333333331000
2333433333333432433333332000
0233310222222212323332210000
0022100111111112222110000000
0011012010011011110000000000
0000010100021101100000000000
0001101100110100210000000000
0112001000200100011000000000
0110000001100010001100000000
0000000000000000000110000000
"""

#: A 5x7 alphabet, only the letters the word needs. Each pixel is two characters
#: wide, because a terminal cell is about twice as tall as it is wide.
GLYPHS = {
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "N": ["#...#", "##..#", "#.#.#", "#.#.#", "#..##", "#...#", "#...#"],
}

WORD = "FLYON"

#: The corner text from the artwork. Kept as real characters so it stays
#: readable at any width and can be selected out of the scrollback.
LEFT_TOP = ("BUILD", "IDEAS", "DEPLOY", "FLYON")
RIGHT_TOP = ("FASTER", "SMARTER", "FURTHER")
LEFT_LOW = ("CODE", "INFRASTRUCTURE", "GROWTH")
RIGHT_LOW = ("SMALL BUGS", "BIG IMPACT")

#: level → (xterm-256 colour, is it bold). Level 0 is the terminal's own black.
RAMP = ((None, False), (22, False), (34, False), (77, False), (156, True))

RESET = "\033[0m"


def paint(level: int, on: bool) -> str:
    colour, bold = RAMP[level]
    if not on or colour is None:
        return ""
    return f"\033[38;5;{colour}m" + ("\033[1m" if bold else "")


def fly_rows(colour: bool = True) -> list[str]:
    """The fly, two pixel rows to a terminal row, through the half block."""
    grid = FLY.strip("\n").split("\n")
    out = []
    for y in range(0, len(grid) - 1, 2):
        top, bottom = grid[y], grid[y + 1]
        line, last = [], None
        for a, b in zip(top, bottom):
            hi, lo = int(a), int(b)
            if hi == 0 and lo == 0:
                char, level = " ", 0
            elif hi and lo:
                char, level = "\u2588", max(hi, lo)     # full block
            elif hi:
                char, level = "\u2580", hi              # upper half
            else:
                char, level = "\u2584", lo              # lower half
            if colour and level != last:
                line.append(RESET if level == 0 else paint(level, True))
                last = level
            line.append(char)
        out.append("".join(line) + (RESET if colour else ""))
    return out


def word_rows(text: str = WORD, colour: bool = True) -> list[str]:
    """The wordmark, seven rows tall, two characters to a pixel."""
    rows = []
    for r in range(7):
        cells = []
        for i, ch in enumerate(text):
            glyph = GLYPHS.get(ch.upper())
            if glyph is None:
                continue
            if i:
                cells.append("  ")
            cells.extend("\u2588\u2588" if p == "#" else "  " for p in glyph[r])
        line = "".join(cells)
        rows.append(f"{paint(4, colour)}{line}{RESET if colour else ''}")
    return rows


def render(width: int = 80, colour: bool = True, corners: bool = True) -> list[str]:
    """
    The whole mark, centred, with the corner text of the original artwork.

    Falls back to the wordmark alone when the terminal is too narrow to hold
    the fly without wrapping it — a wrapped banner looks like a crash.
    """
    def centre(line: str, visible: int) -> str:
        pad = max(0, (width - visible) // 2)
        return " " * pad + line

    dim = f"\033[38;5;{RAMP[1][0]}m" if colour else ""
    mid = f"\033[38;5;{RAMP[2][0]}m" if colour else ""
    end = RESET if colour else ""

    out: list[str] = []
    wide = width >= 64

    if corners and wide:
        for i in range(max(len(LEFT_TOP), len(RIGHT_TOP))):
            left = LEFT_TOP[i] if i < len(LEFT_TOP) else ""
            right = RIGHT_TOP[i] if i < len(RIGHT_TOP) else ""
            out.append(f"{mid}{left}{end}"
                       + " " * max(1, width - len(left) - len(right))
                       + f"{mid}{right}{end}")
        out.append(f"{dim}>{end}")

    if wide:
        out.append("")
        for row in fly_rows(colour):
            out.append(centre(row, 28))

    out.append("")
    for row in word_rows(WORD, colour):
        out.append(centre(row, 58))

    rule = "\u2500" * 24
    out.append("")
    out.append(centre(f"{dim}{rule}{end} {mid}\u2726{end} {dim}{rule}{end}", 51))

    if corners and wide:
        out.append("")
        for i in range(max(len(LEFT_LOW), len(RIGHT_LOW))):
            left = LEFT_LOW[i] if i < len(LEFT_LOW) else ""
            right = RIGHT_LOW[i] if i < len(RIGHT_LOW) else ""
            out.append(f"{mid}{left}{end}"
                       + " " * max(1, width - len(left) - len(right))
                       + f"{mid}{right}{end}")
        out.append(f"{dim}>{end}")
    return out
