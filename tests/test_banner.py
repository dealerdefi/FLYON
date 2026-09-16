"""
The mark, and the instrument it is part of.

A banner is the first thing anybody sees and the last thing anybody tests, which
is how projects end up with a splash screen that wraps into confetti on an
80-column terminal or paints escape codes into a log file.

These are cheap and they cover exactly that: it fits, it degrades, and it gets
out of the way when the output is not a terminal.
"""

from __future__ import annotations

import re
import unittest

from flyon import banner, ui

ANSI = re.compile(r"\033\[[0-9;]*m")


def visible(line: str) -> int:
    return len(ANSI.sub("", line))


class TheArt(unittest.TestCase):
    def test_the_fly_grid_is_rectangular_and_uses_five_levels(self):
        rows = banner.FLY.strip("\n").split("\n")
        self.assertTrue(rows)
        self.assertEqual(len({len(r) for r in rows}), 1, "ragged rows would shear the fly")
        self.assertEqual(len(rows) % 2, 0, "half blocks need an even number of pixel rows")
        self.assertTrue(set("".join(rows)) <= set("01234"))

    def test_it_has_ink_at_both_edges_so_it_centres_on_itself(self):
        rows = banner.FLY.strip("\n").split("\n")
        self.assertTrue(any(r[0] != "0" for r in rows))
        self.assertTrue(any(r[-1] != "0" for r in rows))

    def test_every_letter_of_the_word_has_a_glyph(self):
        for ch in banner.WORD:
            self.assertIn(ch, banner.GLYPHS)

    def test_the_glyphs_are_all_the_same_size(self):
        for name, rows in banner.GLYPHS.items():
            self.assertEqual(len(rows), 7, name)
            self.assertEqual({len(r) for r in rows}, {5}, name)


class Rendering(unittest.TestCase):
    def test_nothing_overflows_the_width_it_was_given(self):
        for width in (64, 72, 80, 96, 100):
            for line in banner.render(width=width):
                self.assertLessEqual(visible(line), width, f"at width {width}: {line!r}")

    def test_a_narrow_terminal_drops_the_fly_rather_than_wrapping_it(self):
        narrow = banner.render(width=60, colour=False)
        wide = banner.render(width=96, colour=False)
        self.assertLess(len(narrow), len(wide))
        for line in narrow:
            self.assertLessEqual(visible(line), 60)

    def test_without_colour_there_is_not_one_escape_code(self):
        for line in banner.render(width=90, colour=False):
            self.assertNotIn("\033", line)

    def test_with_colour_every_line_that_paints_also_resets(self):
        for line in banner.render(width=90, colour=True):
            if "\033[38" in line:
                self.assertIn(banner.RESET, line, line.encode("unicode_escape"))

    def test_the_word_is_still_the_word(self):
        """A pixel alphabet is easy to break silently; count the ink per letter."""
        rows = banner.word_rows("FLYON", colour=False)
        self.assertEqual(len(rows), 7)
        ink = sum(r.count("█") for r in rows)
        expected = sum(sum(row.count("#") for row in banner.GLYPHS[c]) for c in "FLYON") * 2
        self.assertEqual(ink, expected)

    def test_the_fly_comes_back_as_half_the_pixel_rows(self):
        grid = banner.FLY.strip("\n").split("\n")
        self.assertEqual(len(banner.fly_rows(colour=False)), len(grid) // 2)


class TheInstrument(unittest.TestCase):
    """The shared drawing code. A ragged frame is the tell of a fake dashboard."""

    def test_a_bar_is_exactly_the_width_asked_for(self):
        for value, scale in ((0, 1), (0.5, 1), (1, 1), (3, 1), (-1, 1), (1, 0)):
            self.assertEqual(ui.visible(ui.bar(value, scale, 20)), 20, (value, scale))
            self.assertEqual(ui.visible(ui.diverging(value, scale, 20)), 20, (value, scale))

    def test_a_bar_is_drawn_to_the_scale_it_was_given(self):
        half = ui.visible(ui.bar(5, 10, 16).rstrip())
        full = ui.visible(ui.bar(10, 10, 16).rstrip())
        self.assertLess(half, full)
        self.assertEqual(full, 16)

    def test_a_frame_is_square_however_much_colour_is_inside(self):
        rows = ["plain", ui.c("coloured", "green"), ui.c("bold", "gold", bold=True) + " tail"]
        for line in ui.frame(rows, 70):
            self.assertEqual(ui.visible(line), 70, repr(line))

    def test_columns_never_exceed_their_width(self):
        spec = [(6, "<"), (10, ">"), (4, "^")]
        for line in ui.columns([["a very long cell indeed", ui.c("12345", "green"), "x"]], spec):
            self.assertEqual(ui.visible(line), 6 + 10 + 4 + 4)

    def test_flex_leaves_room_for_the_frame(self):
        w = 96
        fixed = [3, 42, 2, 11, 8]
        rest = ui.flex(ui.inner(w), fixed, gap=1)
        self.assertLessEqual(sum(fixed) + rest + len(fixed), ui.inner(w) + 1)

    def test_a_spark_is_one_character_per_value(self):
        self.assertEqual(ui.visible(ui.spark([1, 2, 3, 4])), 4)
        self.assertEqual(ui.spark([]), "")
        self.assertEqual(ui.visible(ui.spark([7, 7, 7])), 3)

    def test_the_record_strip_is_capped(self):
        self.assertEqual(ui.visible(ui.strip([True] * 100, cap=12)), 12)


if __name__ == "__main__":
    unittest.main()
