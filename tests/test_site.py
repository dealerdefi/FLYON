"""
The page, and the two things it will not do.

**It will not render a number it was not given.** The template carries a single
data marker and nothing numeric, so there is no edit to the HTML that changes
what the page claims.

**It will not quietly render demo data.** A file that describes an invented
market must be rendered by a template that says so, and `render` raises rather
than let the band be dropped. That refusal is the whole reason the demo is safe
to ship.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from flyon.chains import NETWORKS
from flyon.demo import DemoChain, demo_network
from flyon.index import scan
from flyon.ledger import Verdict
from flyon.pnl import build as build_pnl
from flyon.rpc import Chain
from flyon.signals import Score
from flyon.site import BAND_ID, MARKER, Data, TemplateRefused, build_site, render

TEMPLATE = Path(__file__).resolve().parent.parent / "site" / "index.template.html"


def small_data(demo: bool = False) -> dict:
    net = demo_network()
    d = DemoChain(blocks=3_000)
    sc = scan(Chain(call=d, expect_chain_id=net.chain_id), net,
              d.start_block, d.head_block, step=5_000)
    wallets = build_pnl(sc.trades)
    return Data(net=net, scan=sc, wallets=wallets, score=Score(signals=[]),
                ledger=Verdict(True, 3, "ab" * 32), demo=demo).build()


class TheTemplate(unittest.TestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text(encoding="utf-8")

    def test_it_exists_and_carries_the_marker(self):
        self.assertIn(MARKER, self.html)

    def test_it_carries_the_demo_band(self):
        self.assertIn(BAND_ID, self.html)

    def test_it_contains_no_number_of_its_own(self):
        """
        Every figure a reader sees must come from the data file. Anything
        numeric left in the markup is a number nobody can check.
        """
        body = self.html.split("<script")[0]
        body = re.sub(r"<style.*?</style>", "", body, flags=re.S)   # CSS may have sizes
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", body)
        # A repository URL is allowed to carry digits — an account name is not a
        # figure. Everything else that a reader could mistake for data is not.
        text = re.sub(r"\S*github\.com\S*", " ", text)
        self.assertNotRegex(text, r"\d", f"a number is baked into the markup: {text.strip()[:120]}")

    def test_it_is_one_file_with_nothing_fetched_from_anywhere(self):
        self.assertNotIn("http://", self.html)
        for tag in ("<script src=", "<link rel=\"stylesheet\""):
            self.assertNotIn(tag, self.html)


class Rendering(unittest.TestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text(encoding="utf-8")

    def test_the_payload_lands_where_the_marker_was(self):
        out = render(self.html, {"demo": False, "hello": "world"})
        self.assertNotIn(MARKER, out)
        self.assertIn('"hello":"world"', out)

    def test_a_template_without_the_marker_is_refused(self):
        with self.assertRaises(TemplateRefused) as caught:
            render("<html>no marker here</html>", {"demo": False})
        self.assertIn("Refusing", str(caught.exception))

    def test_demo_data_in_a_template_without_the_band_is_refused(self):
        stripped = self.html.replace(BAND_ID, 'id="something-else"')
        with self.assertRaises(TemplateRefused) as caught:
            render(stripped, {"demo": True})
        self.assertIn("does not say so", str(caught.exception))

    def test_the_same_template_renders_live_data_without_the_band(self):
        """Only the demo needs the band, and only the demo is refused without it."""
        stripped = self.html.replace(BAND_ID, 'id="something-else"')
        render(stripped, {"demo": False})

    def test_a_token_named_like_a_script_tag_cannot_escape_the_data_block(self):
        evil = {"demo": False, "token": "</script><script>alert(1)</script>"}
        out = render(self.html, evil)
        after = out.split(MARKER)[0] if MARKER in out else out
        payload = out.split('id="flyon-data">')[1].split("</script>")[0]
        self.assertNotIn("</script>", payload)
        self.assertIn("<\\/script>", payload)


class TheDataFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flyon-site-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_demo_file_says_so_in_its_own_json(self):
        self.assertIs(small_data(demo=True)["demo"], True)
        self.assertIn("not real", small_data(demo=True)["network"]["name"])

    def test_it_carries_the_caveats_the_page_must_print(self):
        data = small_data()
        self.assertGreaterEqual(len(data["caveats"]), 5)
        joined = " ".join(data["caveats"]).lower()
        for must_mention in ("dollars", "gas", "airdrop", "advice"):
            self.assertIn(must_mention, joined)

    def test_every_row_carries_a_link_to_the_explorer(self):
        data = small_data()
        for row in data["leaderboard"]:
            self.assertTrue(row["url"].startswith("http"))
        for row in data["feed"]:
            self.assertTrue(row["url"].startswith("http"))

    def test_building_writes_a_page_a_data_file_and_the_fly(self):
        written = build_site(small_data(demo=True), TEMPLATE, self.tmp)
        self.assertTrue(written["page"].exists())
        self.assertTrue(written["data"].exists())
        page = written["page"].read_text(encoding="utf-8")
        self.assertIn(BAND_ID, page)
        self.assertNotIn(MARKER, page)
        again = json.loads(written["data"].read_text(encoding="utf-8"))
        self.assertIs(again["demo"], True)

    def test_the_numbers_on_the_page_are_the_numbers_in_the_data(self):
        data = small_data()
        written = build_site(data, TEMPLATE, self.tmp)
        page = written["page"].read_text(encoding="utf-8")
        payload = json.loads(page.split('id="flyon-data">')[1].split("</script>")[0]
                             .replace("<\\/", "</"))
        self.assertEqual(payload["summary"]["trades"], data["summary"]["trades"])
        self.assertEqual(payload["leaderboard"], data["leaderboard"])


if __name__ == "__main__":
    unittest.main()
