"""
Calls, settlement, and the ledger.

These tests exist because the easiest way to build a signals page that looks
brilliant is to cheat at the scoring, and every way of cheating is a small,
reasonable-looking change to one of these functions:

  - settle from the best price in the window instead of the first one after it
  - quietly drop the calls that did not settle
  - compute a wallet's confidence from outcomes it could not have known yet
  - settle a call twice and keep the better answer

Each of those has a test here that fails if someone makes it.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from flyon.index import Trade
from flyon.ledger import Ledger, verify
from flyon.signals import (DEFAULT_WINDOW, FLAT, Score, Signal, emit, price_of,
                          settle_all, settle_one)


def t(wallet, base, quote, block, pool="0xpool", index=0, symbol="TKN"):
    return Trade(wallet=wallet, pool=pool, block=block, tx=f"0x{block:04x}", index=index,
                 base_symbol=symbol, base_amount=base, quote_symbol="Q",
                 quote_amount=quote, priced=True)


def buy(wallet, qty, cost, block, **kw):
    return t(wallet, +qty, -cost, block, **kw)


def sell(wallet, qty, got, block, **kw):
    return t(wallet, -qty, +got, block, **kw)


class Prices(unittest.TestCase):
    def test_price_is_quote_over_base_either_way_round(self):
        self.assertAlmostEqual(price_of(buy("0xa", 100, 2.0, 1)), 0.02)
        self.assertAlmostEqual(price_of(sell("0xa", 100, 2.0, 1)), 0.02)

    def test_an_unpriced_trade_has_no_price(self):
        x = buy("0xa", 100, 2.0, 1)
        x.priced = False
        self.assertIsNone(price_of(x))


class Settling(unittest.TestCase):
    WINDOW = 100

    def sig(self, entry=0.02, block=10):
        return Signal(wallet="0xa", token="TKN", quote="Q", pool="0xpool",
                      block=block, tx="0x1", entry=entry, size=1.0, confidence=0.5)

    def test_it_settles_from_the_first_trade_after_the_window(self):
        trades = [
            buy("0xb", 100, 3.0, 109),    # inside the window — must be ignored
            buy("0xb", 100, 4.0, 110),    # the first one at or after → 0.04
            buy("0xb", 100, 9.0, 111),    # better, and must not be chosen
        ]
        s = settle_one(self.sig(), trades, window=self.WINDOW)
        self.assertTrue(s.settled)
        self.assertAlmostEqual(s.exit, 0.04)
        self.assertAlmostEqual(s.change, 1.0)
        self.assertTrue(s.outcome)

    def test_a_window_with_no_trade_leaves_the_call_open_forever(self):
        s = settle_one(self.sig(), [buy("0xb", 100, 4.0, 50)], window=self.WINDOW)
        self.assertFalse(s.settled)
        self.assertIsNone(s.outcome)
        self.assertIn("no trade", s.reason)

    def test_a_settled_call_is_never_settled_again(self):
        trades = [buy("0xb", 100, 4.0, 110)]
        s = settle_one(self.sig(), trades, window=self.WINDOW)
        first = (s.exit, s.outcome)
        settle_one(s, [buy("0xb", 100, 99.0, 110)], window=self.WINDOW)
        self.assertEqual((s.exit, s.outcome), first)

    def test_a_flat_move_is_not_a_hit(self):
        s = settle_one(self.sig(entry=0.02),
                       [buy("0xb", 100, 2.0 * (1 + FLAT / 2), 110)], window=self.WINDOW)
        self.assertTrue(s.settled)
        self.assertFalse(s.outcome)

    def test_a_drop_is_a_miss_and_is_kept(self):
        s = settle_one(self.sig(entry=0.02), [buy("0xb", 100, 1.0, 110)], window=self.WINDOW)
        self.assertTrue(s.settled)
        self.assertFalse(s.outcome)
        self.assertAlmostEqual(s.change, -0.5)

    def test_another_pool_cannot_settle_this_call(self):
        s = settle_one(self.sig(), [buy("0xb", 100, 9.0, 110, pool="0xelsewhere")],
                       window=self.WINDOW)
        self.assertFalse(s.settled)


class Confidence(unittest.TestCase):
    def test_a_wallet_with_no_history_starts_at_a_coin(self):
        sigs = emit([buy("0xa", 100, 1.0, 10)], {"0xa"}, window=50)
        self.assertEqual(len(sigs), 1)
        self.assertAlmostEqual(sigs[0].confidence, 0.5)

    def test_confidence_never_uses_an_outcome_from_the_future(self):
        """
        The classic way to make a backtest shine. The second call is made at
        block 20, when the first has not yet settled, so it must still be 0.5 —
        not raised by a result that arrives at block 110.
        """
        trades = [buy("0xa", 100, 1.0, 10), buy("0xa", 100, 1.0, 20),
                  buy("0xb", 100, 50.0, 110), buy("0xb", 100, 50.0, 120)]
        sigs = emit(trades, {"0xa"}, window=100)
        self.assertEqual([round(s.confidence, 4) for s in sigs], [0.5, 0.5])

    def test_a_settled_win_lifts_the_next_call_but_not_to_certainty(self):
        trades = [buy("0xa", 100, 1.0, 10),     # settles from block 110 at 0.50 → hit
                  buy("0xb", 100, 50.0, 110),
                  buy("0xa", 100, 50.0, 300)]   # made later, so it can see that result
        sigs = emit(trades, {"0xa"}, window=100)
        self.assertAlmostEqual(sigs[0].confidence, 0.5)
        self.assertGreater(sigs[1].confidence, 0.5)
        self.assertLess(sigs[1].confidence, 1.0)

    def test_only_watched_wallets_make_calls(self):
        trades = [buy("0xa", 100, 1.0, 10), buy("0xzzz", 100, 1.0, 11)]
        self.assertEqual([s.wallet for s in emit(trades, {"0xa"}, window=50)], ["0xa"])

    def test_a_sell_is_not_a_call(self):
        self.assertEqual(emit([sell("0xa", 100, 1.0, 10)], {"0xa"}, window=50), [])


class TheScoreboard(unittest.TestCase):
    def score(self, outcomes, confidence=0.5):
        rows = []
        for i, o in enumerate(outcomes):
            s = Signal(wallet="0xa", token="T", quote="Q", pool="0xp", block=i, tx="0x",
                       entry=1.0, size=1.0, confidence=confidence)
            s.settled, s.outcome, s.change = True, o, (0.5 if o else -0.5)
            s.exit_block = i + 1
            rows.append(s)
        return Score(signals=rows)

    def test_the_hit_rate_counts_the_misses_too(self):
        self.assertAlmostEqual(self.score([True, True, False, False]).hit_rate, 0.5)

    def test_an_open_call_is_never_counted_as_anything(self):
        s = self.score([True])
        s.signals.append(Signal(wallet="0xa", token="T", quote="Q", pool="0xp", block=9,
                                tx="0x", entry=1.0, size=1.0, confidence=0.9))
        self.assertEqual(len(s.settled), 1)
        self.assertEqual(len(s.open), 1)
        self.assertAlmostEqual(s.hit_rate, 1.0)
        self.assertIn("still open", s.headline())

    def test_the_gap_shows_a_board_flattering_itself(self):
        s = self.score([True, False, False, False], confidence=0.8)
        self.assertAlmostEqual(s.hit_rate, 0.25)
        self.assertAlmostEqual(s.said, 0.8)
        self.assertAlmostEqual(s.gap, 0.55)

    def test_brier_is_the_square_of_the_miss(self):
        self.assertAlmostEqual(self.score([True], confidence=1.0).brier, 0.0)
        self.assertAlmostEqual(self.score([False], confidence=1.0).brier, 1.0)
        self.assertAlmostEqual(self.score([True], confidence=0.5).brier, 0.25)

    def test_with_nothing_settled_it_claims_nothing(self):
        s = Score(signals=[])
        self.assertIsNone(s.hit_rate)
        self.assertIsNone(s.brier)
        self.assertIn("no record to show", s.headline())


class TheLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flyon-test-"))
        self.path = self.tmp / "flyon.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_call_recorded_before_the_outcome_cannot_be_edited_after_it(self):
        led = Ledger(self.path)
        led.append("signal", id="s1", wallet="0xa", entry=0.02, confidence=0.4)
        led.append("settled", id="s1", outcome=False, change=-0.3)
        self.assertTrue(verify(self.path).ok)

        lines = self.path.read_text().splitlines()
        d = json.loads(lines[0])
        d["body"]["confidence"] = 0.9          # flatter the old call
        lines[0] = json.dumps(d, separators=(",", ":"), sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n")

        v = verify(self.path)
        self.assertFalse(v.ok)
        self.assertIn("edited", v.reason)

    def test_deleting_a_losing_call_is_caught(self):
        led = Ledger(self.path)
        for i in range(4):
            led.append("signal", id=f"s{i}")
        lines = self.path.read_text().splitlines()
        del lines[2]
        self.path.write_text("\n".join(lines) + "\n")
        self.assertFalse(verify(self.path).ok)

    def test_an_unknown_kind_is_refused_before_it_is_written(self):
        led = Ledger(self.path)
        with self.assertRaises(ValueError):
            led.append("prediction", id="s1")
        self.assertEqual(len(led), 0)

    def test_a_whole_rewrite_still_verifies_and_the_docs_say_so(self):
        """Tamper-evident, not tamper-proof. The README makes the same claim."""
        led = Ledger(self.path)
        led.append("signal", id="s1")
        self.path.unlink()
        again = Ledger(self.path)
        again.append("signal", id="invented")
        self.assertTrue(verify(self.path).ok)


if __name__ == "__main__":
    unittest.main()
