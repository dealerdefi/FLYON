"""
Profit — and the part every fake leaderboard leaves out.

A wallet's profit on a token is the quote asset it got back minus the quote
asset it paid, matched buy-to-sell in the order the buys happened. FIFO, the
same convention an accountant would use, chosen because it is the one that needs
no assumptions.

    bought  400,000 PEPE for 0.80 WMOCK
    bought  200,000 PEPE for 0.50 WMOCK
    sold    300,000 PEPE for 0.90 WMOCK   → cost of those 300,000 was 0.60
                                          → realised +0.30 WMOCK

## What this cannot know, and says so

**Tokens that arrived without a buy.** An airdrop, a bridge, a transfer from the
person's other wallet. They have no cost basis on this chain, and selling them
looks like pure profit to anything counting naively. FLYON tracks the quantity it
never saw bought and marks the position `unknown basis`; the proceeds from those
units are reported **separately** and are never folded into the profit figure.

This single rule is most of the difference between an honest board and a
flattering one. A wallet that bridged in a bag and dumped it is the most
profitable wallet on any naive tracker ever built.

**Open positions.** What is still held is worth whatever it is worth, and this
file will not guess. `realised` is closed trades only. `holding` is a quantity,
not a valuation.

**Fees and gas.** Not counted. Gas is paid in the native token and is not in a
swap log; including a guess at it would be inventing numbers.

**Wash trading.** Two wallets bouncing a token between themselves can post any
profit they like. Nothing computed from a public chain can tell you that did not
happen, and this file does not pretend otherwise — `docs/PNL.md` says it plainly
and the site links to it from the leaderboard heading.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .index import Trade

#: Smaller than this in quote units and a position is closed, not "dust open".
EPSILON = 1e-12


@dataclass
class Lot:
    """
    A parcel of tokens bought at one price.

    The **unit price is stored, and the cost is derived** — never the other way
    round. Carrying `cost` and subtracting from it as the lot is eaten looks
    equivalent and is not: after a dozen partial sells the quantity lands on
    something like 4e-11 while the cost keeps a residue, and `cost / qty` then
    reports a unit price off by ten orders of magnitude. One sale of dust after
    that books a four-figure profit that never happened.

    That bug was in this file, found by the demo, and `test_pnl.py` now pins the
    invariant it broke: realised profit can never exceed what came in, and can
    never be worse than everything paid.
    """

    qty: float
    unit: float          # quote per base, fixed at the moment of purchase

    @property
    def cost(self) -> float:
        return self.qty * self.unit


@dataclass
class Position:
    """One wallet's history in one token, in one quote asset."""

    wallet: str
    token: str
    quote: str
    lots: deque[Lot] = field(default_factory=deque)

    realised: float = 0.0
    spent: float = 0.0
    received: float = 0.0
    buys: int = 0
    sells: int = 0

    #: Quantity sold that was never seen being bought.
    sold_without_basis: float = 0.0
    #: Quote received for those units. Reported apart, never added to `realised`.
    proceeds_without_basis: float = 0.0

    first_block: int = 0
    last_block: int = 0

    @property
    def holding(self) -> float:
        return sum(l.qty for l in self.lots)

    @property
    def cost_of_holding(self) -> float:
        return sum(l.cost for l in self.lots)

    @property
    def unknown_basis(self) -> bool:
        return self.sold_without_basis > 0

    @property
    def closed(self) -> bool:
        return self.holding <= EPSILON

    def buy(self, qty: float, cost: float, block: int) -> None:
        if qty <= 0:
            return
        cost = max(cost, 0.0)
        self.lots.append(Lot(qty=qty, unit=cost / qty))
        self.spent += cost
        self.buys += 1
        self._stamp(block)

    def sell(self, qty: float, proceeds: float, block: int) -> None:
        if qty <= 0:
            return
        self.sells += 1
        self.received += max(proceeds, 0.0)
        self._stamp(block)

        # Dust is relative to the size of the sale, not an absolute number.
        # A token with eighteen decimals trades in millions, and an absolute
        # epsilon leaves lots alive at 4e-11 that then poison the arithmetic.
        dust = max(qty * 1e-12, 1e-18)

        remaining = qty
        matched_cost = 0.0
        matched_qty = 0.0
        while remaining > dust and self.lots:
            lot = self.lots[0]
            take = min(lot.qty, remaining)
            matched_cost += lot.unit * take
            matched_qty += take
            lot.qty -= take
            remaining -= take
            if lot.qty <= dust:
                self.lots.popleft()

        share = (matched_qty / qty) if qty else 0.0
        self.realised += proceeds * share - matched_cost

        if remaining > EPSILON:
            # Sold more than we ever saw bought. Those units came from somewhere
            # this chain cannot show us, so their proceeds are quarantined.
            self.sold_without_basis += remaining
            self.proceeds_without_basis += proceeds * (remaining / qty if qty else 0.0)

    def _stamp(self, block: int) -> None:
        self.first_block = self.first_block or block
        self.last_block = max(self.last_block, block)

    def row(self) -> dict:
        return {
            "wallet": self.wallet, "token": self.token, "quote": self.quote,
            "realised": round(self.realised, 12),
            "spent": round(self.spent, 12), "received": round(self.received, 12),
            "buys": self.buys, "sells": self.sells,
            "holding": round(self.holding, 12),
            "cost_of_holding": round(self.cost_of_holding, 12),
            "unknown_basis": self.unknown_basis,
            "sold_without_basis": round(self.sold_without_basis, 12),
            "proceeds_without_basis": round(self.proceeds_without_basis, 12),
            "first_block": self.first_block, "last_block": self.last_block,
            "closed": self.closed,
        }


@dataclass
class Wallet:
    """Everything one address did, summed across its tokens."""

    address: str
    positions: list[Position] = field(default_factory=list)

    @property
    def realised(self) -> float:
        return sum(p.realised for p in self.positions)

    @property
    def spent(self) -> float:
        return sum(p.spent for p in self.positions)

    @property
    def trades(self) -> int:
        return sum(p.buys + p.sells for p in self.positions)

    @property
    def tokens(self) -> int:
        return len(self.positions)

    @property
    def wins(self) -> int:
        return sum(1 for p in self.positions if p.closed and p.realised > 0)

    @property
    def losses(self) -> int:
        return sum(1 for p in self.positions if p.closed and p.realised < 0)

    @property
    def hit_rate(self) -> float | None:
        settled = self.wins + self.losses
        return self.wins / settled if settled else None

    @property
    def unknown_basis(self) -> bool:
        """True if any part of this wallet's history cannot be accounted for."""
        return any(p.unknown_basis for p in self.positions)

    @property
    def quarantined(self) -> float:
        return sum(p.proceeds_without_basis for p in self.positions)

    @property
    def roi(self) -> float | None:
        """Realised over spent. None when nothing was ever paid for."""
        return self.realised / self.spent if self.spent > EPSILON else None

    @property
    def last_block(self) -> int:
        return max((p.last_block for p in self.positions), default=0)

    def row(self) -> dict:
        return {
            "wallet": self.address,
            "realised": round(self.realised, 12),
            "spent": round(self.spent, 12),
            "roi": None if self.roi is None else round(self.roi, 6),
            "trades": self.trades, "tokens": self.tokens,
            "wins": self.wins, "losses": self.losses,
            "hit_rate": None if self.hit_rate is None else round(self.hit_rate, 4),
            "unknown_basis": self.unknown_basis,
            "quarantined": round(self.quarantined, 12),
            "last_block": self.last_block,
        }


# ── building it ──────────────────────────────────────────────────────────────


def build(trades: list[Trade]) -> dict[str, Wallet]:
    """
    Every wallet's book, from the trades, in chain order.

    Only priced, attributed trades take part. The rest are visible in the feed
    and deliberately absent here — a row nobody can price is not profit.
    """
    books: dict[tuple[str, str, str], Position] = {}
    for t in sorted(trades, key=lambda x: (x.block, x.index)):
        if not (t.priced and t.wallet):
            continue
        key = (t.wallet, t.base_symbol, t.quote_symbol)
        pos = books.get(key)
        if pos is None:
            pos = books[key] = Position(wallet=t.wallet, token=t.base_symbol,
                                        quote=t.quote_symbol)
        if t.base_amount > 0:
            pos.buy(t.base_amount, t.spent, t.block)
        elif t.base_amount < 0:
            pos.sell(-t.base_amount, t.got, t.block)

    wallets: dict[str, Wallet] = defaultdict(lambda: Wallet(address=""))
    for (addr, _tok, _q), pos in books.items():
        w = wallets[addr]
        w.address = addr
        w.positions.append(pos)
    return dict(wallets)


def leaderboard(wallets: dict[str, Wallet], limit: int = 50,
                min_trades: int = 2, include_unknown_basis: bool = False) -> list[Wallet]:
    """
    Top by realised profit.

    Wallets with any unaccounted units are **left out by default**. They are not
    accused of anything — the chain simply cannot show where those tokens came
    from, so their profit is not a number this tool is willing to rank. Pass
    `include_unknown_basis` to see them, and the site labels every such row.
    """
    rows = [w for w in wallets.values() if w.trades >= min_trades]
    if not include_unknown_basis:
        rows = [w for w in rows if not w.unknown_basis]
    rows.sort(key=lambda w: (-w.realised, w.address))
    return rows[:limit]
