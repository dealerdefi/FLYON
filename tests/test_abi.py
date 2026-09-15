"""
Decoding, and the sign that decides everything.

A swap log says how much of each token moved and in which direction. Read the
direction backwards and every buy becomes a sell — the feed goes green when it
should go red, the leaderboard inverts, and nothing about the page looks broken.
So the sign gets tested from both sides, in both pool layouts, for both AMM
versions.

`keccak.py` is tested against the published vectors, including the most-quoted
thirty-two bytes in the ecosystem: the `Transfer` topic.
"""

from __future__ import annotations

import unittest

from flyon.abi import (SWAP_V2, SWAP_V3, TRANSFER, Swap, decode, decode_swap,
                      decode_transfer, strip_str, to_address, to_int, to_uint, topic)
from flyon.demo import abi_string, word_addr, word_int, word_uint
from flyon.keccak import eip55, keccak256


class Keccak(unittest.TestCase):
    #: From the Keccak team's own vectors and from any block explorer.
    VECTORS = {
        b"": "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        b"abc": "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
    }

    def test_the_published_vectors(self):
        for data, want in self.VECTORS.items():
            self.assertEqual(keccak256(data).hex(), want, data)

    def test_it_is_keccak_and_not_sha3(self):
        """One padding byte apart, and the difference is every event topic."""
        import hashlib

        self.assertNotEqual(keccak256(b"abc").hex(), hashlib.sha3_256(b"abc").hexdigest())

    def test_the_event_topics_are_the_ones_on_chain(self):
        self.assertEqual(
            TRANSFER, "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
        self.assertEqual(
            SWAP_V2, "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822")
        self.assertEqual(
            SWAP_V3, "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67")

    def test_a_long_input_crosses_the_rate_boundary(self):
        # 136 bytes is one full block; 200 forces a second permutation.
        self.assertEqual(len(keccak256(b"x" * 200)), 32)
        self.assertNotEqual(keccak256(b"x" * 135), keccak256(b"x" * 136))

    def test_eip55_matches_the_reference_addresses(self):
        for low, want in {
            "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359": "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        }.items():
            self.assertEqual(eip55(low), want)

    def test_topic_of_a_signature_is_stable(self):
        self.assertEqual(topic("Transfer(address,address,uint256)"), TRANSFER)


class Words(unittest.TestCase):
    def test_two_s_complement_round_trips(self):
        for v in (0, 1, -1, 2**200, -(2**200), -12345678901234567890):
            self.assertEqual(to_int(word_int(v)), v)

    def test_an_unsigned_word_is_never_negative(self):
        self.assertEqual(to_uint(word_uint(2**255)), 2**255)

    def test_an_address_is_the_last_twenty_bytes_lowercased(self):
        self.assertEqual(to_address(word_addr("0xAAbbCCddEEff00112233445566778899aabbccdd")),
                         "0xaabbccddeeff00112233445566778899aabbccdd")

    def test_an_abi_string_comes_back(self):
        for s in ("WETH", "a-very-long-token-symbol-over-32-bytes-long", ""):
            self.assertEqual(strip_str(abi_string(s)), s)


def swap_log(pool, to_addr, a0, a1, block=100, version=3, index=0):
    if version == 3:
        data = "0x" + word_int(a0) + word_int(a1) + word_uint(0) * 3
        top = SWAP_V3
    else:
        in0, out0 = (a0, 0) if a0 > 0 else (0, -a0)
        in1, out1 = (a1, 0) if a1 > 0 else (0, -a1)
        data = "0x" + word_uint(in0) + word_uint(in1) + word_uint(out0) + word_uint(out1)
        top = SWAP_V2
    return {"address": pool, "topics": [top, word_addr("0x" + "dd" * 20), word_addr(to_addr)],
            "data": data, "blockNumber": hex(block),
            "transactionHash": "0x" + "ab" * 32, "logIndex": hex(index)}


class Swaps(unittest.TestCase):
    POOL = "0x" + "cc" * 20
    WHO = "0x" + "a1" * 20

    def test_a_v3_swap_keeps_the_pool_s_signs(self):
        s = decode_swap(swap_log(self.POOL, self.WHO, a0=+1000, a1=-5))
        self.assertIsNotNone(s)
        self.assertEqual((s.amount0, s.amount1), (1000, -5))
        self.assertEqual(s.version, 3)
        self.assertEqual(s.to, self.WHO)

    def test_a_v2_swap_is_folded_into_the_same_shape(self):
        s = decode_swap(swap_log(self.POOL, self.WHO, a0=+1000, a1=-5, version=2))
        self.assertEqual((s.amount0, s.amount1), (1000, -5))
        self.assertEqual(s.version, 2)

    def test_both_versions_describe_the_same_trade_identically(self):
        a = decode_swap(swap_log(self.POOL, self.WHO, +7, -3, version=3))
        b = decode_swap(swap_log(self.POOL, self.WHO, +7, -3, version=2))
        self.assertEqual((a.amount0, a.amount1), (b.amount0, b.amount1))

    def test_a_log_that_is_not_a_swap_decodes_to_nothing(self):
        self.assertIsNone(decode_swap({"address": self.POOL, "topics": ["0x00"], "data": "0x"}))
        self.assertIsNone(decode_swap({"address": self.POOL, "topics": [], "data": "0x"}))

    def test_a_truncated_swap_is_refused_rather_than_read_as_zero(self):
        bad = swap_log(self.POOL, self.WHO, 1, 1)
        bad["data"] = "0x" + word_int(1)          # one word where two are needed
        self.assertIsNone(decode_swap(bad))


class Transfers(unittest.TestCase):
    TOKEN = "0x" + "bb" * 20

    def log(self, value, topics=3):
        t = [TRANSFER, word_addr("0x" + "11" * 20), word_addr("0x" + "22" * 20)]
        return {"address": self.TOKEN, "topics": t[:topics],
                "data": "0x" + word_uint(value), "blockNumber": "0x1",
                "transactionHash": "0x" + "cd" * 32, "logIndex": "0x0"}

    def test_a_transfer_decodes(self):
        t = decode_transfer(self.log(4200))
        self.assertEqual(t.value, 4200)
        self.assertEqual(t.to, "0x" + "22" * 20)

    def test_a_two_topic_log_is_not_a_transfer(self):
        self.assertIsNone(decode_transfer(self.log(1, topics=2)))

    def test_decode_sorts_the_two_kinds_and_drops_zero_transfers(self):
        logs = [swap_log("0x" + "cc" * 20, "0x" + "a1" * 20, 1, -1), self.log(0), self.log(5)]
        swaps, transfers = decode(logs)
        self.assertEqual((len(swaps), len(transfers)), (1, 1))
        self.assertIsInstance(swaps[0], Swap)


if __name__ == "__main__":
    unittest.main()
