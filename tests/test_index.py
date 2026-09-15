"""
The indexer, the RPC layer, and the three things they refuse to guess.

No test in this file opens a socket. The chain is either the demo transport or a
recorded tape, which is also how you would reproduce a bug from a week ago.
"""

from __future__ import annotations

import unittest

from flyon.abi import SWAP_V3
from flyon.chains import NETWORKS, Network, pick_quote
from flyon.demo import DemoChain, demo_network, word_addr, word_int, word_uint
from flyon.index import Pools, Trade, scan, to_trade
from flyon.rpc import READ_METHODS, Chain, Replay, RpcError, key_of

QUOTE = "0x" + "11" * 20
BASE = "0x" + "b1" * 20
POOL = "0x" + "c1" * 20
WHO = "0x" + "a1" * 20


class TheRpcLayerOnlyReads(unittest.TestCase):
    def test_every_method_it_knows_is_a_read(self):
        for m in READ_METHODS:
            self.assertTrue(m.startswith("eth_get") or m in ("eth_chainId", "eth_blockNumber",
                                                             "eth_call"), m)

    def test_the_file_contains_no_way_to_send_a_transaction(self):
        """Asserting the absence of a feature, by reading the source for it."""
        import ast
        import inspect

        from flyon import rpc

        tree = ast.parse(inspect.getsource(rpc))
        tree.body = [n for n in tree.body
                     if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                             and isinstance(n.value.value, str))]
        code = ast.unparse(tree)
        for forbidden in ("sendRawTransaction", "sendTransaction", "eth_sign",
                          "personal_", "private_key", "privateKey", "eth_accounts"):
            self.assertNotIn(forbidden, code, f"{forbidden} must not exist in rpc.py")

    def test_a_write_method_is_refused_by_both_transports(self):
        for transport in (Replay(tape={}), DemoChain(blocks=500)):
            with self.assertRaises(RpcError):
                transport("eth_sendRawTransaction", ["0xdeadbeef"])

    def test_the_wrong_chain_is_refused_before_anything_is_read(self):
        d = DemoChain(blocks=500)
        ch = Chain(call=d, expect_chain_id=999)
        with self.assertRaises(RpcError) as caught:
            ch.head()
        self.assertIn("999", str(caught.exception))
        self.assertIn("Refusing", str(caught.exception))

    def test_a_replay_never_invents_an_answer(self):
        r = Replay(tape={})
        with self.assertRaises(RpcError):
            r("eth_blockNumber", [])

    def test_a_recorded_run_replays_to_the_same_trades(self):
        net = demo_network()
        d = DemoChain(blocks=4_000)
        live = scan(Chain(call=d, expect_chain_id=net.chain_id),
                    net, d.start_block, d.start_block + 4_000, step=2_000)

        replayed = scan(Chain(call=Replay(tape=d.tape), expect_chain_id=net.chain_id),
                        net, d.start_block, d.start_block + 4_000, step=2_000)
        self.assertEqual([t.row() for t in live.trades], [t.row() for t in replayed.trades])
        self.assertGreater(len(live.trades), 0)

    def test_the_cache_key_is_stable_across_runs(self):
        self.assertEqual(key_of("eth_getLogs", [{"b": 2, "a": 1}]),
                         key_of("eth_getLogs", [{"a": 1, "b": 2}]))


def pool_with(quotes: dict) -> tuple[Network, Pools, DemoChain]:
    net = Network(key="t", name="t", chain_id=1337, rpc="", explorer="https://x.invalid",
                  quotes=quotes)

    class Fake(DemoChain):
        def __post_init__(self):
            self.logs = [1]  # skip generation
            self.head_block = 1
            self.tokens = {QUOTE: ("QUOTE", 18), BASE: ("BASE", 6)}
            self.pools = {POOL: (BASE, QUOTE)}

    f = Fake()
    return net, Pools(Chain(call=f), net), f


class Pricing(unittest.TestCase):
    def test_a_pool_with_a_known_quote_is_priced(self):
        net, pools, _ = pool_with({QUOTE: ("QUOTE", 18, 0)})
        p = pools.pool(POOL)
        self.assertTrue(p.priced)
        self.assertEqual(p.quote.symbol, "QUOTE")
        self.assertEqual(p.base.symbol, "BASE")

    def test_a_pool_with_no_known_quote_is_carried_unpriced(self):
        net, pools, _ = pool_with({})
        p = pools.pool(POOL)
        self.assertFalse(p.priced)

    def test_the_lower_ranked_quote_wins_when_a_pool_has_two(self):
        net = NETWORKS["demo"]
        n = Network(key="x", name="x", chain_id=1, rpc="", explorer="",
                    quotes={"0xa": ("A", 18, 3), "0xb": ("B", 6, 0)})
        self.assertEqual(pick_quote(n, "0xa", "0xb"), (1, "B"))
        self.assertEqual(pick_quote(n, "0xb", "0xa"), (0, "B"))
        self.assertIsNone(pick_quote(n, "0xc", "0xd"))

    def test_a_token_that_will_not_say_its_decimals_stays_raw_and_is_flagged(self):
        net, pools, fake = pool_with({QUOTE: ("QUOTE", 18, 0)})
        fake.tokens.pop(BASE)                      # answers nothing
        p = pools.pool(POOL)
        self.assertIsNone(p.base.decimals)
        self.assertFalse(p.base.known)


class TheSign(unittest.TestCase):
    """A buy read as a sell inverts the whole page and looks completely fine."""

    def swap(self, a0, a1, layout=(BASE, QUOTE)):
        from flyon.abi import decode_swap

        net, pools, fake = pool_with({QUOTE: ("QUOTE", 18, 0)})
        fake.pools = {POOL: layout}
        log = {"address": POOL,
               "topics": [SWAP_V3, word_addr("0x" + "dd" * 20), word_addr(WHO)],
               "data": "0x" + word_int(a0) + word_int(a1) + word_uint(0) * 3,
               "blockNumber": "0x64", "transactionHash": "0x" + "ab" * 32, "logIndex": "0x0"}
        return to_trade(decode_swap(log), pools.pool(POOL), set())

    def test_quote_into_the_pool_is_a_buy(self):
        # base out of the pool (negative), quote into it (positive) → the
        # trader received base and paid quote
        t = self.swap(a0=-1_000_000, a1=+2 * 10**18)
        self.assertEqual(t.side, "buy")
        self.assertGreater(t.base_amount, 0)
        self.assertAlmostEqual(t.quote_amount, -2.0)
        self.assertAlmostEqual(t.spent, 2.0)
        self.assertEqual(t.got, 0.0)

    def test_base_into_the_pool_is_a_sell(self):
        t = self.swap(a0=+1_000_000, a1=-2 * 10**18)
        self.assertEqual(t.side, "sell")
        self.assertLess(t.base_amount, 0)
        self.assertAlmostEqual(t.quote_amount, 2.0)
        self.assertAlmostEqual(t.got, 2.0)
        self.assertEqual(t.spent, 0.0)

    def test_the_answer_does_not_change_when_the_pool_is_the_other_way_round(self):
        straight = self.swap(a0=-1_000_000, a1=+2 * 10**18, layout=(BASE, QUOTE))
        flipped = self.swap(a0=+2 * 10**18, a1=-1_000_000, layout=(QUOTE, BASE))
        self.assertEqual(straight.side, flipped.side)
        self.assertAlmostEqual(straight.base_amount, flipped.base_amount)
        self.assertAlmostEqual(straight.quote_amount, flipped.quote_amount)


class Attribution(unittest.TestCase):
    def test_a_router_is_never_credited_with_the_trade(self):
        from flyon.abi import Swap
        from flyon.index import attribute

        router = "0x" + "99" * 20
        s = Swap(pool=POOL, sender=WHO, to=router, amount0=1, amount1=-1,
                 block=1, tx="0x", index=0, version=3)
        self.assertEqual(attribute(s, {router}), WHO)

    def test_a_trade_nobody_can_be_credited_with_is_kept_but_unattributed(self):
        from flyon.abi import Swap
        from flyon.index import attribute

        zero = "0x" + "00" * 20
        s = Swap(pool=POOL, sender=zero, to=zero, amount0=1, amount1=-1,
                 block=1, tx="0x", index=0, version=3)
        self.assertIsNone(attribute(s, set()))


class ScanningTheDemo(unittest.TestCase):
    """Built once for the class — generating the market is the slow part."""

    @classmethod
    def setUpClass(cls):
        cls.net = demo_network()
        cls.demo = DemoChain(blocks=6_000)

    def setUp(self):
        self.net = self.demo_net = self.__class__.net
        self.d = self.__class__.demo
        self.d.calls = 0
        self.ch = Chain(call=self.d, expect_chain_id=self.net.chain_id)

    def test_it_reads_every_swap_it_generated(self):
        s = scan(self.ch, self.net, self.d.start_block, self.d.head_block, step=5_000)
        self.assertEqual(s.swaps_seen, len(self.d.logs))
        self.assertEqual(len(s.trades), len(self.d.logs))

    def test_the_step_size_changes_nothing_but_the_call_count(self):
        a = scan(self.ch, self.net, self.d.start_block, self.d.head_block, step=40_000)
        b = scan(self.ch, self.net, self.d.start_block, self.d.head_block, step=1_000)
        self.assertEqual([t.row() for t in a.trades], [t.row() for t in b.trades])

    def test_trades_come_back_in_chain_order(self):
        s = scan(self.ch, self.net, self.d.start_block, self.d.head_block, step=5_000)
        order = [(t.block, t.index) for t in s.trades]
        self.assertEqual(order, sorted(order))

    def test_a_pool_is_asked_about_its_tokens_exactly_once(self):
        pools = Pools(self.ch, self.net)
        scan(self.ch, self.net, self.d.start_block, self.d.head_block, step=5_000, pools=pools)
        before = self.d.calls
        pools.pool(next(iter(pools.pools)))
        self.assertEqual(self.d.calls, before)


if __name__ == "__main__":
    unittest.main()
