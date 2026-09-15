"""
Profit.

Every leaderboard in this corner of the internet is an arithmetic claim about
strangers' money, and almost none of them can be checked. These tests are the
part of FLYON that earns the right to publish one.

The first class is invariants: statements that must hold for *any* sequence of
trades, however adversarial. They are what caught the bug that shipped in the
first draft of `pnl.py` — a lot whose cost drifted away from its quantity until
a sale of dust booked a four-figure profit out of nothing.
"""

from __future__ import annotations

import unittest

from flyon.index import Trade
from flyon.pnl import Position, Wallet, build, leaderboard


def trade(wallet, base, quote, block, index=0, symbol="TKN", q="WETH", priced=True):
    return Trade(wallet=wallet, pool="0xpool", block=block, tx=f"0x{block:064x}",
                 index=index, base_symbol=symbol, base_amount=base,
                 quote_symbol=q, quote_amount=quote, priced=priced)


def buy(wallet, qty, cost, block, **kw):
    return trade(wallet, +qty, -cost, block, **kw)


def sell(wallet, qty, proceeds, block, **kw):
    return trade(wallet, -qty, +proceeds, block, **kw)


class Invariants(unittest.TestCase):
    """Hold for any history, including ones built to break the arithmetic."""

    def positions(self, trades):
        return [p for w in build(trades).values() for p in w.positions]

    def test_profit_can_never_exceed_what_came_in(self):
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(1000, 2.0, 1)
        pos.sell(1000, 3.0, 2)
        self.assertLessEqual(pos.realised, pos.received + 1e-12)

    def test_loss_can_never_be_worse_than_everything_paid(self):
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(1000, 5.0, 1)
        pos.sell(1000, 0.0, 2)
        self.assertGreaterEqual(pos.realised, -pos.spent - 1e-12)
        self.assertAlmostEqual(pos.realised, -5.0)

    def test_many_partial_sells_do_not_invent_profit(self):
        """
        The regression. A lot eaten in a hundred slices used to leave a quantity
        near zero beside a cost that had not shrunk with it, and `cost / qty`
        then reported a unit price off by ten orders of magnitude.
        """
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(1_000_000.0, 10.0, 1)
        for i in range(100):
            pos.sell(10_000.0, 0.1, i + 2)
        self.assertAlmostEqual(pos.realised, 0.0, places=6)
        self.assertLessEqual(pos.realised, pos.received + 1e-9)
        self.assertLess(abs(pos.holding), 1e-6)

    def test_alternating_buys_and_sells_stay_bounded(self):
        trades = []
        for i in range(200):
            trades.append(buy("0xa", 1_000.0 * (i % 7 + 1), 0.5, 2 * i))
            trades.append(sell("0xa", 800.0 * (i % 5 + 1), 0.4, 2 * i + 1))
        for pos in self.positions(trades):
            self.assertLessEqual(pos.realised, pos.received + 1e-9, "profit above proceeds")
            self.assertGreaterEqual(pos.realised, -pos.spent - 1e-9, "loss below everything paid")

    def test_a_token_with_six_decimals_behaves_like_one_with_eighteen(self):
        small = Position(wallet="0xa", token="USDC", quote="Q")
        large = Position(wallet="0xa", token="WETH", quote="Q")
        for i in range(50):
            small.buy(1_000.0, 1.0, i)
            large.buy(1_000_000_000_000.0, 1.0, i)
        for i in range(50):
            small.sell(1_000.0, 1.1, 100 + i)
            large.sell(1_000_000_000_000.0, 1.1, 100 + i)
        self.assertAlmostEqual(small.realised, large.realised, places=6)


class Fifo(unittest.TestCase):
    def test_the_worked_example_from_the_docstring(self):
        pos = Position(wallet="0xa", token="PEPE", quote="WETH")
        pos.buy(400_000, 0.80, 1)
        pos.buy(200_000, 0.50, 2)
        pos.sell(300_000, 0.90, 3)
        # the first 300,000 cost 0.60 of the 0.80 parcel
        self.assertAlmostEqual(pos.realised, 0.30, places=9)
        self.assertAlmostEqual(pos.holding, 300_000)

    def test_the_oldest_parcel_goes_first(self):
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(100, 1.0, 1)      # 0.01 each
        pos.buy(100, 5.0, 2)      # 0.05 each
        pos.sell(100, 2.0, 3)
        self.assertAlmostEqual(pos.realised, 1.0)   # 2.0 − 1.0, not 2.0 − 5.0

    def test_a_position_is_closed_only_when_nothing_is_left(self):
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(100, 1.0, 1)
        pos.sell(40, 0.5, 2)
        self.assertFalse(pos.closed)
        pos.sell(60, 0.8, 3)
        self.assertTrue(pos.closed)


class UnknownBasis(unittest.TestCase):
    """The rule that separates an honest board from a flattering one."""

    def test_selling_what_was_never_bought_is_quarantined_not_counted(self):
        pos = Position(wallet="0xa", token="AIRDROP", quote="Q")
        pos.sell(1_000_000, 50.0, 1)
        self.assertAlmostEqual(pos.realised, 0.0)
        self.assertAlmostEqual(pos.proceeds_without_basis, 50.0)
        self.assertTrue(pos.unknown_basis)

    def test_a_partly_bought_bag_splits_cleanly(self):
        pos = Position(wallet="0xa", token="T", quote="Q")
        pos.buy(100, 1.0, 1)
        pos.sell(200, 4.0, 2)            # half accounted for, half from nowhere
        self.assertAlmostEqual(pos.realised, 2.0 - 1.0)
        self.assertAlmostEqual(pos.sold_without_basis, 100)
        self.assertAlmostEqual(pos.proceeds_without_basis, 2.0)

    def test_such_a_wallet_is_off_the_board_by_default(self):
        trades = [
            buy("0xhonest", 100, 1.0, 1), sell("0xhonest", 100, 2.0, 2),
            sell("0xghost", 1_000_000, 500.0, 3), buy("0xghost", 10, 0.1, 4),
            sell("0xghost", 10, 0.2, 5),
        ]
        wallets = build(trades)
        board = leaderboard(wallets, limit=10, min_trades=1)
        self.assertEqual([w.address for w in board], ["0xhonest"])

        withall = leaderboard(wallets, limit=10, min_trades=1, include_unknown_basis=True)
        self.assertIn("0xghost", [w.address for w in withall])
        ghost = wallets["0xghost"]
        self.assertAlmostEqual(ghost.quarantined, 500.0)
        self.assertLess(ghost.realised, 1.0)

    def test_the_naive_board_would_have_ranked_the_ghost_first(self):
        """Stated as a test because it is the reason the rule exists."""
        trades = [buy("0xhonest", 100, 1.0, 1), sell("0xhonest", 100, 2.0, 2),
                  sell("0xghost", 1_000_000, 500.0, 3)]
        wallets = build(trades)
        naive = sorted(wallets.values(), key=lambda w: -(w.realised + w.quarantined))
        self.assertEqual(naive[0].address, "0xghost")
        self.assertEqual(leaderboard(wallets, min_trades=1)[0].address, "0xhonest")


class WhatIsCounted(unittest.TestCase):
    def test_an_unpriced_trade_never_reaches_the_book(self):
        trades = [buy("0xa", 100, 1.0, 1, priced=False), sell("0xa", 100, 9.0, 2, priced=False)]
        self.assertEqual(build(trades), {})

    def test_a_trade_with_no_wallet_never_reaches_the_book(self):
        trades = [trade(None, 100, -1.0, 1), trade(None, -100, 9.0, 2)]
        self.assertEqual(build(trades), {})

    def test_trades_are_applied_in_chain_order_not_list_order(self):
        early = buy("0xa", 100, 1.0, block=1, index=0)
        late = sell("0xa", 100, 3.0, block=9, index=0)
        forwards = build([early, late])["0xa"].realised
        backwards = build([late, early])["0xa"].realised
        self.assertAlmostEqual(forwards, backwards)
        self.assertAlmostEqual(forwards, 2.0)

    def test_each_token_is_its_own_book(self):
        trades = [buy("0xa", 100, 1.0, 1, symbol="ONE"), sell("0xa", 100, 2.0, 2, symbol="ONE"),
                  buy("0xa", 100, 5.0, 3, symbol="TWO"), sell("0xa", 100, 1.0, 4, symbol="TWO")]
        w = build(trades)["0xa"]
        self.assertEqual(w.tokens, 2)
        self.assertAlmostEqual(w.realised, 1.0 - 4.0)
        self.assertEqual((w.wins, w.losses), (1, 1))
        self.assertAlmostEqual(w.hit_rate, 0.5)

    def test_roi_is_none_rather_than_infinite_when_nothing_was_paid(self):
        w = Wallet(address="0xa")
        self.assertIsNone(w.roi)


if __name__ == "__main__":
    unittest.main()
