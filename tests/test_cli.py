"""
The command line, run the way a person would.

The demo is the end-to-end test: it invents a market, reads it with the real
indexer, prices it, scores it, writes the ledger and builds the page. If any
seam between those is wrong, `flyon demo` says so here.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from flyon.cli import main
from flyon.ledger import verify


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flyon-cli-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_flyon(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            try:
                code = main(["--home", str(self.tmp), *argv])
            except SystemExit as e:
                code = int(e.code or 0)
        return code, out.getvalue()


class Demo(CliCase):
    def test_it_runs_end_to_end_and_writes_everything(self):
        code, out = self.run_flyon("demo", "--blocks", "4000", "--window", "2000")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tmp / "trades.json").exists())
        self.assertTrue((self.tmp / "flyon.jsonl").exists())
        self.assertTrue((self.tmp / "site" / "index.html").exists())
        self.assertTrue((self.tmp / "site" / "data.json").exists())
        self.assertTrue((self.tmp / "site" / "fly.png").exists())

    def test_the_page_it_builds_carries_the_demo_band(self):
        self.run_flyon("demo", "--blocks", "4000", "--window", "2000")
        page = (self.tmp / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("DEMO DATA", page)
        data = json.loads((self.tmp / "site" / "data.json").read_text(encoding="utf-8"))
        self.assertIs(data["demo"], True)

    def test_the_same_seed_gives_the_same_board(self):
        self.run_flyon("demo", "--seed", "99", "--blocks", "4000", "--window", "2000")
        first = json.loads((self.tmp / "site" / "data.json").read_text())["leaderboard"]
        shutil.rmtree(self.tmp / "site")
        (self.tmp / "flyon.jsonl").unlink()
        self.run_flyon("demo", "--seed", "99", "--blocks", "4000", "--window", "2000")
        second = json.loads((self.tmp / "site" / "data.json").read_text())["leaderboard"]
        self.assertEqual(first, second)

    def test_a_different_seed_gives_a_different_market(self):
        self.run_flyon("demo", "--seed", "1", "--blocks", "4000", "--window", "2000")
        a = json.loads((self.tmp / "site" / "data.json").read_text())["summary"]["trades"]
        shutil.rmtree(self.tmp / "site")
        (self.tmp / "flyon.jsonl").unlink()
        self.run_flyon("demo", "--seed", "2", "--blocks", "4000", "--window", "2000")
        b = json.loads((self.tmp / "site" / "data.json").read_text())["summary"]["trades"]
        self.assertNotEqual(a, b)

    def test_its_ledger_verifies(self):
        self.run_flyon("demo", "--blocks", "4000", "--window", "2000")
        v = verify(self.tmp / "flyon.jsonl")
        self.assertTrue(v.ok, v.reason)
        self.assertGreater(v.lines, 0)

    def test_every_call_it_recorded_is_in_the_ledger_before_its_outcome(self):
        self.run_flyon("demo", "--blocks", "4000", "--window", "2000")
        lines = [json.loads(l) for l in
                 (self.tmp / "flyon.jsonl").read_text().splitlines() if l.strip()]
        made = {e["body"]["id"]: e["seq"] for e in lines if e["kind"] == "signal"}
        for e in lines:
            if e["kind"] == "settled":
                self.assertIn(e["body"]["id"], made)
                self.assertLess(made[e["body"]["id"]], e["seq"],
                                "a settlement was written before the call it settles")


class Reading(CliCase):
    def setUp(self):
        super().setUp()
        self.run_flyon("demo", "--blocks", "4000", "--window", "2000")

    def test_the_board_prints_and_names_what_it_left_off(self):
        code, out = self.run_flyon("board", "--limit", "5")
        self.assertEqual(code, 0)
        self.assertIn("realised", out)
        self.assertIn("never saw them buy", out)

    def test_the_board_can_show_them_when_asked(self):
        _, out = self.run_flyon("board", "--all", "--limit", "40")
        self.assertIn("⚑", out)

    def test_the_feed_prints_buys_and_sells(self):
        _, out = self.run_flyon("feed", "--limit", "20")
        self.assertIn("BUY", out)
        self.assertIn("SELL", out)

    def test_the_score_reports_the_misses_too(self):
        _, out = self.run_flyon("score", "--window", "2000")
        self.assertIn("hit rate", out)
        self.assertIn("brier", out)

    def test_doctor_says_what_it_can_and_cannot_reach(self):
        code, out = self.run_flyon("doctor", "--network", "demo")
        self.assertEqual(code, 0)
        self.assertIn("all of them reads", out)
        self.assertIn("unbroken", out)

    def test_the_site_can_be_rebuilt_without_touching_a_chain(self):
        page = self.tmp / "site" / "index.html"
        page.unlink()
        code, _ = self.run_flyon("site", "--window", "2000")
        self.assertEqual(code, 0)
        self.assertTrue(page.exists())


class NothingIndexedYet(CliCase):
    def test_it_says_what_to_run_instead_of_failing_bare(self):
        code, out = self.run_flyon("board")
        self.assertEqual(code, 1)
        self.assertIn("nothing indexed yet", out)
        self.assertIn("flyon demo", out)


if __name__ == "__main__":
    unittest.main()
