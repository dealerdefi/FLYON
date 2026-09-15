"""
Signals that mark their own homework.

Every tracker on the internet shows you what a wallet just bought. None of them
show you what happened next, because the honest version of that number is
usually unflattering and nobody is obliged to publish it.

FLYON is obliged, by construction: a signal is written into the append-only
ledger at the block it was seen, and the **same code** settles it later from the
same chain. There is no path through this file that produces a hit rate without
also producing the misses.

## A signal is a fact, never a forecast

    0x4a…c1 bought 412,000 PEPE at 0.0000021 WMOCK   block 9,412,881

That is an observation. FLYON never says the price will rise, and nothing here
constitutes advice — `docs/SIGNALS.md` and the site say so in those words.

What *is* a claim, and what gets graded, is the implied one: **that this wallet's
buys are followed by a higher price more often than a coin would be.** Each
signal carries a confidence before the outcome is known, and that confidence is
not a mood — it is the wallet's own settled hit rate so far, Laplace-smoothed so
a wallet with two lucky calls does not arrive at 100%.

## Settling

A signal settles `window` blocks after it was seen, by reading the price out of
the same pool's later swaps. Price is the same arithmetic as everywhere else —
quote paid over base received.

**If the pool did not trade again inside the window, the signal does not
settle.** It stays open, forever if need be. Reaching for the last known price
from before the window, or the nearest trade after it, is how a scoreboard
quietly selects the outcomes that suit it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .index import Trade

#: How long a call gets to work, in blocks. Set once, in the config, and moved
#: only with the ledger showing it moved.
DEFAULT_WINDOW = 7_200

#: A move smaller than this either way is called flat, and flat is not a hit.
FLAT = 0.005

#: Laplace smoothing: a wallet starts at a coin and has to earn its way off it.
PRIOR_HITS, PRIOR_N = 1.0, 2.0


def price_of(trade: Trade) -> float | None:
    """Quote per unit of base, from one trade. None when it cannot be taken."""
    if not trade.priced or not trade.base_amount:
        return None
    p = abs(trade.quote_amount) / abs(trade.base_amount)
    return p if p > 0 else None


@dataclass
class Signal:
    wallet: str
    token: str
    quote: str
    pool: str
    block: int
    tx: str
    entry: float
    size: float            # quote asset spent
    confidence: float      # the wallet's own settled hit rate, before this call

    settled: bool = False
    exit: float | None = None
    exit_block: int | None = None
    change: float | None = None     # exit/entry − 1
    outcome: bool | None = None     # True when the move cleared FLAT upward
    reason: str = ""

    @property
    def id(self) -> str:
        return f"{self.tx}:{self.block}:{self.wallet[-6:]}"

    def row(self) -> dict:
        return {
            "id": self.id, "wallet": self.wallet, "token": self.token,
            "quote": self.quote, "pool": self.pool, "block": self.block,
            "tx": self.tx, "entry": self.entry, "size": round(self.size, 12),
            "confidence": round(self.confidence, 4),
            "settled": self.settled, "exit": self.exit, "exit_block": self.exit_block,
            "change": None if self.change is None else round(self.change, 6),
            "outcome": self.outcome, "reason": self.reason,
        }


def brier(p: float, outcome: bool) -> float:
    return (p - (1.0 if outcome else 0.0)) ** 2


# ── making them ──────────────────────────────────────────────────────────────


def emit(trades: list[Trade], watched: set[str], window: int = DEFAULT_WINDOW,
         min_size: float = 0.0) -> list[Signal]:
    """
    Every buy by a watched wallet becomes a signal, in chain order.

    Confidence is computed from that wallet's signals that have already settled
    *at the moment this one is made* — never from the future, which is the
    mistake that makes a backtest look brilliant and a live run look nothing
    like it.
    """
    watched = {w.lower() for w in watched}
    rows = sorted(trades, key=lambda t: (t.block, t.index))
    priced = [t for t in rows if t.priced and t.wallet]

    out: list[Signal] = []
    for t in rows:
        if not (t.priced and t.wallet and t.wallet.lower() in watched):
            continue
        if t.base_amount <= 0 or t.spent < min_size:
            continue
        entry = price_of(t)
        if entry is None:
            continue

        earlier = [s for s in out if s.wallet == t.wallet and s.block + window <= t.block]
        settle_all(earlier, priced, window)
        hits = sum(1 for s in earlier if s.outcome)
        n = sum(1 for s in earlier if s.settled)

        out.append(Signal(
            wallet=t.wallet, token=t.base_symbol, quote=t.quote_symbol, pool=t.pool,
            block=t.block, tx=t.tx, entry=entry, size=t.spent,
            confidence=(hits + PRIOR_HITS) / (n + PRIOR_N),
        ))
    return out


def settle_one(sig: Signal, trades: list[Trade], window: int = DEFAULT_WINDOW) -> Signal:
    """
    Read what happened, once the window has closed.

    The exit price is the **first** trade in the same pool at or after
    `block + window`. First, not best, not last — picking among them is
    the whole game, and this function does not get to play.
    """
    if sig.settled:
        return sig

    target = sig.block + window
    later = [t for t in trades
             if t.pool == sig.pool and t.block >= target and price_of(t) is not None]
    if not later:
        sig.reason = f"no trade in this pool at or after block {target}"
        return sig

    first = min(later, key=lambda t: (t.block, t.index))
    exit_price = price_of(first)
    assert exit_price is not None

    sig.exit = exit_price
    sig.exit_block = first.block
    sig.change = exit_price / sig.entry - 1.0
    sig.outcome = sig.change > FLAT
    sig.settled = True
    sig.reason = "settled from the first trade after the window"
    return sig


def settle_all(signals: list[Signal], trades: list[Trade],
               window: int = DEFAULT_WINDOW) -> list[Signal]:
    for s in signals:
        settle_one(s, trades, window)
    return signals


# ── the scoreboard ───────────────────────────────────────────────────────────


@dataclass
class Score:
    signals: list[Signal] = field(default_factory=list)

    @property
    def settled(self) -> list[Signal]:
        return [s for s in self.signals if s.settled]

    @property
    def open(self) -> list[Signal]:
        return [s for s in self.signals if not s.settled]

    @property
    def hits(self) -> int:
        return sum(1 for s in self.settled if s.outcome)

    @property
    def hit_rate(self) -> float | None:
        return self.hits / len(self.settled) if self.settled else None

    @property
    def said(self) -> float | None:
        s = self.settled
        return sum(x.confidence for x in s) / len(s) if s else None

    @property
    def gap(self) -> float | None:
        """said − hit rate. Positive means the board flatters itself."""
        a, b = self.said, self.hit_rate
        return None if a is None or b is None else a - b

    @property
    def brier(self) -> float | None:
        s = self.settled
        return sum(brier(x.confidence, bool(x.outcome)) for x in s) / len(s) if s else None

    @property
    def median_change(self) -> float | None:
        moves = sorted(s.change for s in self.settled if s.change is not None)
        if not moves:
            return None
        mid = len(moves) // 2
        return moves[mid] if len(moves) % 2 else (moves[mid - 1] + moves[mid]) / 2

    def headline(self) -> str:
        """The line the site is not allowed to render without."""
        if not self.settled:
            return f"{len(self.open)} calls open · none settled yet · no record to show"
        bits = [f"{len(self.settled)} settled", f"hit {self.hit_rate * 100:.0f}%"]
        if self.brier is not None:
            bits.append(f"brier {self.brier:.3f}")
        if self.median_change is not None:
            bits.append(f"median {self.median_change * 100:+.1f}%")
        if self.open:
            bits.append(f"{len(self.open)} still open")
        return "  ·  ".join(bits)

    def row(self) -> dict:
        return {
            "signals": len(self.signals),
            "settled": len(self.settled), "open": len(self.open),
            "hits": self.hits,
            "hit_rate": None if self.hit_rate is None else round(self.hit_rate, 4),
            "said": None if self.said is None else round(self.said, 4),
            "gap": None if self.gap is None else round(self.gap, 4),
            "brier": None if self.brier is None else round(self.brier, 4),
            "median_change": None if self.median_change is None else round(self.median_change, 6),
            "headline": self.headline(),
        }
