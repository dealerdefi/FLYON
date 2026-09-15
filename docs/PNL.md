# How profit is computed

A wallet's profit on a token is the quote asset it got back minus the quote
asset it paid, matched buy-to-sell in the order the buys happened.

```
bought  400,000 PEPE for 0.80 WETH
bought  200,000 PEPE for 0.50 WETH
sold    300,000 PEPE for 0.90 WETH   → those 300,000 cost 0.60
                                     → realised +0.30 WETH
```

First-in-first-out, the convention an accountant would use, chosen because it is
the one that needs no assumptions.

## In what

**The pool's quote asset. Never dollars.**

A dollar figure needs a price feed. A price feed is somebody's API, with a
number you cannot re-derive, and the moment a leaderboard depends on one it is a
leaderboard you have to take on faith. Quote-asset profit comes out of the same
swap logs as everything else, so a stranger with an RPC endpoint can recompute
every row on the page.

If neither side of a pool is a registered quote asset, trades in it are **read
and counted but not priced**: they appear in the feed, they are absent from
profit, and the reason is shown. Routing through two more pools to invent a
price is how you end up confidently ranking someone who never made a cent.

### Registering one

```bash
flyon index --quote 0xADDRESS:SYMBOL:DECIMALS
```

The list ships empty. That is deliberate: an address copied from a forum is how
a tracker ends up denominating everything in a token that is not what its ticker
says. You decide what counts as money, and the page prints which asset every
figure is in.

## What it cannot know

### Tokens that arrived without a buy

An airdrop, a bridge, a transfer from the owner's other wallet. They have no
cost basis on this chain, and selling them looks like pure profit to anything
counting naively.

FLYON tracks the quantity it never saw bought, reports those proceeds
**separately**, and leaves the wallet off the board.

This single rule is most of the difference between an honest board and a
flattering one. On the demo market it sets aside 223.94 WMOCK against a top
honest wallet on +4.350 — a naive board would have ranked the quarantined
wallet first by fifty to one.

It is not an accusation, and the page says so where the number appears. The
chain cannot show where those tokens came from; that is the whole statement.

### Open positions

`realised` is closed trades only. What a wallet still holds is reported as a
quantity and never valued, because valuing it means picking a price, and picking
a price means picking the flattering one.

### Gas and fees

Not counted. Gas is paid in the native token and is not in a swap log.
Estimating it would be inventing a number.

### Wash trading

Two wallets bouncing a token between themselves can post any profit they like.
Nothing computed from a public chain can tell you that did not happen. FLYON does
not pretend otherwise; the page links here from the leaderboard heading.

### Who anybody is

FLYON ranks addresses. It does not attach names to them. A public page linking a
person to a wallet is a different product with different consequences, and this
one does not do it.

## The bug that shaped this file

The first draft stored each parcel's `cost` and subtracted from it as the parcel
was sold. That looks equivalent to storing the unit price and is not: after a
dozen partial sells the quantity lands near `4e-11` while the cost keeps a
residue, and `cost / qty` then reports a unit price off by ten orders of
magnitude. One sale of dust books a four-figure profit that never happened.

The demo market found it — a wallet showed +9,523 on 122 spent — and the fix was
to store the unit price and derive the cost. `test_pnl.py` now pins the
invariant that was broken:

> realised profit can never exceed what came in, and can never be worse than
> everything paid

for any sequence of trades, including ones written to break it.

It is worth saying plainly why that matters: nothing about that number looked
wrong on the page. It had a wallet address, a trade count, and a rank. The only
thing standing between a bug like that and a published lie is a test that knows
what is impossible.
