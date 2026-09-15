"""
A chain that is not a chain.

`flyon demo` invents a small market — a few pools, a few dozen wallets, a few
thousand swaps — and answers JSON-RPC calls about it. **The real indexer then
reads it the way it would read anything**: same log decoding, same pool lookups,
same signed-amount arithmetic, same PnL. There is no shortcut from here to the
numbers on the page.

Two reasons it exists.

**So the site renders before the first real block is indexed.** A dashboard
opened on an empty chain teaches nobody anything.

**So the numbers in the README are reproducible.** Same seed, same market, same
leaderboard, on any machine, with no network.

## It shouts

Every artefact this produces is stamped. The data file carries `"demo": true`,
the network is called `the demo chain (not real)`, its explorer links go to
`example.invalid` and resolve to nothing on purpose, and the page paints a band
across the top that is not removable from the data side. The site refuses to
render a demo file without it — `site.py` raises rather than let the banner be
dropped, and a test proves it.

A demo is an honest thing. A demo that could be mistaken for live data is not,
and that distinction is the entire reason this project got built the way it did.
"""

from __future__ import annotations

import json
from math import exp
from dataclasses import dataclass, field
from typing import Any

from .abi import SELECTOR, SWAP_V3
from .keccak import keccak256
from .chains import NETWORKS
from .rpc import key_of

WMOCK = "0x" + "11" * 20

#: (symbol, decimals, how good the wallets trading it tend to look)
TOKENS = [
    ("PEPO", 18), ("FLYCOIN", 18), ("FLYON", 18), ("NECTAR", 6),
    ("COMPOUND", 18), ("LARVA", 9),
]

SEED = 7_331


class Rng:
    """mulberry32 — same seed, same market, on any machine."""

    def __init__(self, seed: int) -> None:
        self.s = seed & 0xFFFFFFFF

    def u32(self) -> int:
        self.s = (self.s + 0x6D2B79F5) & 0xFFFFFFFF
        z = self.s
        z = ((z ^ (z >> 15)) * (z | 1)) & 0xFFFFFFFF
        z ^= (z + ((z ^ (z >> 7)) * (z | 61)) & 0xFFFFFFFF) & 0xFFFFFFFF
        return (z ^ (z >> 14)) & 0xFFFFFFFF

    def f(self) -> float:
        return self.u32() / 0x100000000

    def between(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.f()

    def pick(self, seq):
        return seq[self.u32() % len(seq)]

    def chance(self, p: float) -> bool:
        return self.f() < p


# ── encoding ─────────────────────────────────────────────────────────────────


def word_uint(v: int) -> str:
    return f"{v & ((1 << 256) - 1):064x}"


def word_int(v: int) -> str:
    return word_uint(v if v >= 0 else v + (1 << 256))


def word_addr(a: str) -> str:
    return word_uint(int(a, 16))


def abi_string(s: str) -> str:
    raw = s.encode()
    body = raw.hex().ljust(((len(raw) + 31) // 32) * 64, "0")
    return "0x" + word_uint(32) + word_uint(len(raw)) + body


def address(n: int, tag: int = 0xA0) -> str:
    """
    An address that looks like one.

    Sequential addresses would make the demo's leaderboard obviously synthetic
    at a glance, which sounds like a virtue but is not the one we want: the data
    should be labelled as fake, not *look* fake, or nobody learns what the real
    page will read like. The label does the work — see this module's docstring.
    """
    h = keccak256(f"flyon-demo/{tag:02x}/{n}".encode()).hex()
    return "0x" + f"{tag:02x}" + h[:38]


# ── the market ───────────────────────────────────────────────────────────────


@dataclass
class DemoChain:
    """A JSON-RPC transport over an invented market. Opens no socket, ever."""

    seed: int = SEED
    wallets: int = 34
    blocks: int = 40_000
    start_block: int = 9_400_000

    logs: list[dict] = field(default_factory=list)
    tokens: dict[str, tuple[str, int]] = field(default_factory=dict)
    pools: dict[str, tuple[str, str]] = field(default_factory=dict)
    tape: dict[str, Any] = field(default_factory=dict)
    calls: int = 0

    def __post_init__(self) -> None:
        if not self.logs:
            self.build()

    # ── generation ───────────────────────────────────────────────────────────

    def build(self) -> None:
        rng = Rng(self.seed)
        self.tokens[WMOCK] = ("WMOCK", 18)

        pools = []
        for i, (symbol, decimals) in enumerate(TOKENS):
            token = address(i + 1, 0xB0)
            pool = address(i + 1, 0xC0)
            self.tokens[token] = (symbol, decimals)
            # token0 is the base, token1 the quote — deliberately not always the
            # same way round in real pools, so one is flipped below.
            self.pools[pool] = (token, WMOCK) if i % 2 == 0 else (WMOCK, token)
            pools.append((pool, token, decimals, rng.between(1e-7, 4e-5)))
        base_price = {p[0]: p[3] for p in pools}
        drift = {p[0]: 0.0 for p in pools}

        # A few wallets are simply better than the rest. Nothing here is a
        # prediction — it is a synthetic history for the code to chew on.
        skill = {address(w, 0xA0): rng.between(-0.35, 0.55) for w in range(1, self.wallets + 1)}
        holdings: dict[tuple[str, str], float] = {}
        index = 0
        block = self.start_block

        while block < self.start_block + self.blocks:
            block += 1 + int(rng.between(0, 24))
            pool, token, decimals, _old = rng.pick(pools)
            # Price is an anchored walk on the log, pulled back toward the
            # pool's starting level and clamped. A plain multiplicative random
            # walk over three thousand trades drifts by orders of magnitude and
            # hands the demo a wallet up 9,000% — which would be a lie about
            # what this tool usually shows, even inside something labelled a
            # demo. The clamp keeps any token inside roughly ±3×.
            dev = drift[pool] * 0.985 + rng.between(-0.05, 0.05)
            drift[pool] = max(-1.1, min(1.1, dev))
            price = base_price[pool] * exp(drift[pool])
            for j, p in enumerate(pools):
                if p[0] == pool:
                    pools[j] = (p[0], p[1], p[2], price)

            wallet = rng.pick(list(skill))
            edge = skill[wallet]
            # A good wallet buys when the token is cheap against its anchor and
            # sells when it is dear. That is the only advantage in this market.
            cheap = -drift[pool]
            buying = rng.chance(0.5 + edge * cheap * 0.9)
            held = holdings.get((wallet, token), 0.0)
            if not buying and held <= 0:
                buying = True

            if buying:
                quote_in = rng.between(0.05, 4.0)
                base_out = quote_in / price
                holdings[(wallet, token)] = held + base_out
            else:
                base_in = held * rng.between(0.25, 1.0)
                quote_in = -base_in * price
                base_out = -base_in
                holdings[(wallet, token)] = held - base_in

            # Amounts from the POOL's side: positive in, negative out.
            base_raw = int(round(-base_out * 10 ** decimals))
            quote_raw = int(round(quote_in * 10 ** 18))
            t0, _t1 = self.pools[pool]
            a0, a1 = ((base_raw, quote_raw) if t0 == token else (quote_raw, base_raw))

            index += 1
            self.logs.append({
                "address": pool,
                "topics": [SWAP_V3, word_addr(address(0xD1, 0xD0)), word_addr(wallet)],
                "data": "0x" + word_int(a0) + word_int(a1)
                        + word_uint(0) + word_uint(0) + word_uint(0),
                "blockNumber": hex(block),
                "transactionHash": "0x" + f"{index:064x}",
                "logIndex": hex(index % 256),
            })

        # One wallet is handed tokens it never bought, then sells them. Every
        # naive leaderboard puts this address first; ours quarantines it.
        ghost = address(self.wallets + 1, 0xA0)
        pool, token, decimals, price = pools[0]
        t0, _ = self.pools[pool]
        # It trades a little for real, so it earns a place on a naive board and
        # then sells a bag nobody saw it buy — the exact shape this tool exists
        # to separate out.
        for k in range(4):
            index += 1
            block += 120
            q_in = 0.6 + 0.2 * k
            b_out = q_in / price
            a_base = int(round(-b_out * 10 ** decimals))
            a_quote = int(round(q_in * 10 ** 18))
            a0, a1 = ((a_base, a_quote) if t0 == token else (a_quote, a_base))
            self.logs.append({
                "address": pool,
                "topics": [SWAP_V3, word_addr(address(0xD1, 0xD0)), word_addr(ghost)],
                "data": "0x" + word_int(a0) + word_int(a1)
                        + word_uint(0) + word_uint(0) + word_uint(0),
                "blockNumber": hex(block),
                "transactionHash": "0x" + f"{index:064x}",
                "logIndex": hex(index % 256),
            })
        for k in range(3):
            index += 1
            block += 300
            base_in = 900_000.0 * (k + 1)
            a_base = int(round(base_in * 10 ** decimals))
            a_quote = int(round(-base_in * price * 1.4 * 10 ** 18))
            a0, a1 = ((a_base, a_quote) if t0 == token else (a_quote, a_base))
            self.logs.append({
                "address": pool,
                "topics": [SWAP_V3, word_addr(address(0xD1, 0xD0)), word_addr(ghost)],
                "data": "0x" + word_int(a0) + word_int(a1)
                        + word_uint(0) + word_uint(0) + word_uint(0),
                "blockNumber": hex(block),
                "transactionHash": "0x" + f"{index:064x}",
                "logIndex": hex(index % 256),
            })

        self.head_block = block + 10

    # ── the transport ────────────────────────────────────────────────────────

    def __call__(self, method: str, params: list[Any]) -> Any:
        from .rpc import READ_METHODS, RpcError, hexint

        # The demo transport carries the same refusal as the real one. A fake
        # chain that would happily accept a write is a hole in the guarantee,
        # even if nothing on the other end of it exists.
        if method not in READ_METHODS:
            raise RpcError(f"{method} is not a read method. FLYON only reads")

        self.calls += 1
        if method == "eth_chainId":
            result: Any = hex(NETWORKS["demo"].chain_id)
        elif method == "eth_blockNumber":
            result = hex(self.head_block)
        elif method == "eth_getLogs":
            q = params[0]
            lo, hi = hexint(q["fromBlock"]), hexint(q["toBlock"])
            result = [l for l in self.logs if lo <= hexint(l["blockNumber"]) <= hi]
        elif method == "eth_getBlockByNumber":
            n = hexint(params[0])
            result = {"number": hex(n), "timestamp": hex(1_780_000_000 + n * 2)}
        elif method == "eth_call":
            result = self.view(params[0].get("to", ""), params[0].get("data", ""))
        else:
            result = None

        self.tape[key_of(method, params)] = result
        return result

    def view(self, to: str, data: str) -> str:
        to = to.lower()
        if data == SELECTOR["token0"] and to in self.pools:
            return "0x" + word_addr(self.pools[to][0])
        if data == SELECTOR["token1"] and to in self.pools:
            return "0x" + word_addr(self.pools[to][1])
        if data == SELECTOR["decimals"] and to in self.tokens:
            return "0x" + word_uint(self.tokens[to][1])
        if data == SELECTOR["symbol"] and to in self.tokens:
            return abi_string(self.tokens[to][0])
        return "0x"

    def save_tape(self, path) -> None:
        from pathlib import Path

        Path(path).write_text(json.dumps(self.tape, indent=0, sort_keys=True), encoding="utf-8")


def demo_network():
    """The demo network, with its quote asset registered. Named so nobody confuses it."""
    net = NETWORKS["demo"]
    net.quotes[WMOCK] = ("WMOCK", 18, 0)
    return net
