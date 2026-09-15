"""
Blocks in, trades out.

The indexer walks a range of blocks, pulls every swap log in it, asks each pool
once what its two tokens are, and turns the pool's signed amounts into something
a person can read:

    0xab…3f  BUY   412,000 PEPE  for  0.84 WMOCK   block 9,412,881

That is the whole job. Everything downstream — profit, the leaderboard, the
feed, the signals — is arithmetic over these rows, which is why this file is
careful and boring.

## Three things it refuses to guess

**The trader.** A swap log names the pool's caller and recipient, and on a
router trade both are the router, not the person. So the wallet credited with a
trade is the recipient when it is not a contract we already know to be a router,
and otherwise the transaction's sender. When neither can be established the
trade is recorded with `wallet: null` and left out of the leaderboard.

**The price.** Only pools with a known quote asset are priced. The rest are
carried through unpriced, visible in the feed, absent from profit.

**The decimals.** Read from the token itself with `decimals()`. A token that
does not answer is left at raw units and flagged, never assumed to be 18 —
assuming 18 on a 6-decimal token is a factor of a trillion, which on a
leaderboard looks like a genius.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterator

from .abi import SELECTOR, Swap, decode, strip_str, to_address, to_uint, words
from .chains import Network, pick_quote
from .rpc import Chain, RpcError

#: eth_getLogs answers get refused above a few thousand blocks on most endpoints.
DEFAULT_STEP = 2_000


@dataclass
class Token:
    address: str
    symbol: str = ""
    decimals: int | None = None

    @property
    def known(self) -> bool:
        return self.decimals is not None

    def human(self, raw: int) -> float:
        """Raw units → a number a person reads. Unknown decimals stay raw."""
        return raw / (10 ** self.decimals) if self.decimals is not None else float(raw)


@dataclass
class Pool:
    address: str
    token0: Token
    token1: Token
    #: 0 or 1 — which side is the money. None when the pool cannot be priced.
    quote_side: int | None = None
    quote_symbol: str = ""

    @property
    def priced(self) -> bool:
        return self.quote_side is not None

    @property
    def base(self) -> Token:
        return self.token1 if self.quote_side == 0 else self.token0

    @property
    def quote(self) -> Token:
        return self.token0 if self.quote_side == 0 else self.token1


@dataclass
class Trade:
    """One swap, from the trader's side. Signs are the trader's, not the pool's."""

    wallet: str | None
    pool: str
    block: int
    tx: str
    index: int
    #: what changed hands, in human units, from the trader's point of view
    base_symbol: str
    base_amount: float      # positive = the trader received it
    quote_symbol: str
    quote_amount: float     # negative = the trader paid it
    priced: bool
    note: str = ""

    @property
    def side(self) -> str:
        return "buy" if self.base_amount > 0 else "sell"

    @property
    def spent(self) -> float:
        """How much quote asset left the trader's hands. Zero on a sell."""
        return -self.quote_amount if self.quote_amount < 0 else 0.0

    @property
    def got(self) -> float:
        """How much quote asset came back. Zero on a buy."""
        return self.quote_amount if self.quote_amount > 0 else 0.0

    def row(self) -> dict:
        d = asdict(self)
        d["side"] = self.side
        return d


# ── reading a pool ───────────────────────────────────────────────────────────


class Pools:
    """
    A cache in front of the chain.

    A pool's tokens never change, so each one is asked exactly once per run and
    the answers are kept. On a busy range this is the difference between four
    calls and four thousand.
    """

    def __init__(self, chain: Chain, net: Network) -> None:
        self.chain = chain
        self.net = net
        self.pools: dict[str, Pool] = {}
        self.tokens: dict[str, Token] = {}
        self.misses: dict[str, str] = {}

    def token(self, address: str) -> Token:
        address = address.lower()
        if address in self.tokens:
            return self.tokens[address]

        t = Token(address=address)
        try:
            raw = self.chain.call_contract(address, SELECTOR["decimals"])
            ws = words(raw)
            if ws:
                d = to_uint(ws[0])
                # A sane token is 0..36. Anything else is a contract that
                # answered something other than a decimals(), so it stays None.
                t.decimals = d if 0 <= d <= 36 else None
        except RpcError:
            pass
        try:
            t.symbol = strip_str(self.chain.call_contract(address, SELECTOR["symbol"]))[:12]
        except RpcError:
            pass
        if not t.symbol:
            t.symbol = address[:8]
        self.tokens[address] = t
        return t

    def pool(self, address: str) -> Pool | None:
        address = address.lower()
        if address in self.pools:
            return self.pools[address]
        if address in self.misses:
            return None

        try:
            raw0 = self.chain.call_contract(address, SELECTOR["token0"])
            raw1 = self.chain.call_contract(address, SELECTOR["token1"])
        except RpcError as e:
            self.misses[address] = str(e)
            return None

        w0, w1 = words(raw0), words(raw1)
        if not w0 or not w1:
            # It emitted a Swap but will not name its tokens. Not a pool we can
            # read, and inventing its pair would poison everything downstream.
            self.misses[address] = "did not answer token0/token1"
            return None

        t0, t1 = self.token(to_address(w0[0])), self.token(to_address(w1[0]))
        p = Pool(address=address, token0=t0, token1=t1)
        chosen = pick_quote(self.net, t0.address, t1.address)
        if chosen is not None:
            p.quote_side, p.quote_symbol = chosen
        self.pools[address] = p
        return p


# ── turning swaps into trades ────────────────────────────────────────────────


def attribute(swap: Swap, routers: set[str]) -> str | None:
    """
    Whose trade is this?

    The recipient, unless the recipient is a router we know about — routers
    receive on behalf of people and crediting them would put a contract at the
    top of the leaderboard, which is a classic tell of a tracker nobody checked.
    """
    to = swap.to.lower()
    if to and to not in routers and int(to, 16) != 0:
        return to
    sender = swap.sender.lower()
    if sender and sender not in routers and int(sender, 16) != 0:
        return sender
    return None


def to_trade(swap: Swap, pool: Pool, routers: set[str]) -> Trade:
    """
    The pool's signed amounts, flipped into the trader's.

    A positive pool amount means the token went **into** the pool, so the trader
    gave it up. This one minus sign is the whole difference between a buy and a
    sell, and it is tested from both directions.
    """
    trader0 = -pool.token0.human(swap.amount0)
    trader1 = -pool.token1.human(swap.amount1)

    if pool.priced:
        base_amt = trader1 if pool.quote_side == 0 else trader0
        quote_amt = trader0 if pool.quote_side == 0 else trader1
        base_sym, quote_sym = pool.base.symbol, pool.quote.symbol
        note = "" if pool.base.known and pool.quote.known else "decimals unknown, amounts are raw"
    else:
        # No side is money, so nothing is "the price". Carried, not priced.
        base_amt, quote_amt = trader0, trader1
        base_sym, quote_sym = pool.token0.symbol, pool.token1.symbol
        note = "no known quote asset in this pool — counted, not priced"

    return Trade(
        wallet=attribute(swap, routers),
        pool=pool.address, block=swap.block, tx=swap.tx, index=swap.index,
        base_symbol=base_sym, base_amount=base_amt,
        quote_symbol=quote_sym, quote_amount=quote_amt,
        priced=pool.priced and pool.base.known and pool.quote.known,
        note=note,
    )


@dataclass
class Scan:
    """What one pass over a block range produced, including what it could not read."""

    trades: list[Trade] = field(default_factory=list)
    from_block: int = 0
    to_block: int = 0
    swaps_seen: int = 0
    unreadable_pools: dict[str, str] = field(default_factory=dict)
    unattributed: int = 0

    @property
    def priced(self) -> list[Trade]:
        return [t for t in self.trades if t.priced and t.wallet]

    def summary(self) -> dict:
        return {
            "from_block": self.from_block,
            "to_block": self.to_block,
            "swaps_seen": self.swaps_seen,
            "trades": len(self.trades),
            "priced": len(self.priced),
            "unattributed": self.unattributed,
            "unreadable_pools": len(self.unreadable_pools),
        }


def ranges(start: int, end: int, step: int) -> Iterator[tuple[int, int]]:
    while start <= end:
        stop = min(start + step - 1, end)
        yield start, stop
        start = stop + 1


def scan(chain: Chain, net: Network, from_block: int, to_block: int,
         step: int = DEFAULT_STEP, routers: set[str] | None = None,
         pools: Pools | None = None) -> Scan:
    """Read a block range and return the trades in it, in chain order."""
    routers = {r.lower() for r in (routers or set())}
    pools = pools or Pools(chain, net)
    out = Scan(from_block=from_block, to_block=to_block)

    for lo, hi in ranges(from_block, to_block, step):
        swaps, _transfers = decode(chain.logs(lo, hi))
        out.swaps_seen += len(swaps)
        for swap in swaps:
            p = pools.pool(swap.pool)
            if p is None:
                out.unreadable_pools[swap.pool] = pools.misses.get(swap.pool, "unreadable")
                continue
            t = to_trade(swap, p, routers)
            if t.wallet is None:
                out.unattributed += 1
            out.trades.append(t)

    out.trades.sort(key=lambda t: (t.block, t.index))
    return out
