# Calls, and what happened next

## A call is a fact

```
0x4a…c1 bought 412,000 PEPO at 0.0000021 WMOCK   block 9,412,881
```

That is an observation about something that already happened. FLYON never says a
price will rise. **Nothing here is advice and nothing here is a forecast**, and
the page says so where anyone can see it.

A call is recorded when a watched wallet buys. Watched means near the top of the
board by realised profit, computed by [the rules in PNL.md](PNL.md) — not a list
somebody curated.

## What does get graded

The implied claim: **that these wallets' buys are followed by a higher price
more often than a coin would be.**

Each call carries a confidence before its outcome is known. That number is not a
mood — it is that wallet's own settled hit rate at the moment of the call,
Laplace-smoothed:

```
confidence = (hits + 1) / (settled + 2)
```

so a wallet with two lucky calls arrives at 75%, not 100%, and a wallet with no
history starts at a coin and has to earn its way off it.

**Never from outcomes it could not have known.** The confidence for a call made
at block 20 is computed only from calls that had already settled by block 20.
Reaching forward is the single most common way a backtest comes out brilliant
and a live run comes out looking nothing like it, so there is a test named after
it.

## Settling

A call settles `window` blocks after it was seen — 7,200 by default — from the
price in the same pool.

| | |
|---|---|
| exit price | the **first** trade in that pool at or after `block + window` |
| not | the best price in the window |
| not | the last price in the window |
| not | the nearest trade, whichever side it falls |
| no trade in the window | the call stays **open**, forever if need be |
| already settled | never settled again |

Every one of those alternatives would raise the hit rate, and every one of them
has a test that fails if someone reaches for it. Picking among the prices in a
window is the whole game and this code does not get to play.

A move smaller than 0.5% either way is called flat, and flat is not a hit.

## The score

```
500 settled  ·  hit 46%  ·  brier 0.266  ·  median -1.3%  ·  112 still open
```

**hit** — the share of settled calls that went up. Misses included, obviously;
that is the point.

**said** — the average confidence carried into those calls.

**gap** — `said − hit`. Positive means the board flatters itself, and on the
demo market it does, by 6 points.

**brier** — the mean of `(p − outcome)²`. Zero is perfect, 0.25 is a coin, 1 is
being certain and wrong. It is used because it is *proper*: your best expected
score comes from reporting what you actually believe. Saying 95% to look
decisive scores better on the days you are right and much worse on the days you
are not, and the arithmetic works out against you. Saying 50% on everything
parks you at 0.25 forever, which is exactly a coin's score.

There is no way to look good at this except by being right, or by being honest
about how right you expect to be.

**Open calls are never counted as anything.** With nothing settled, the page
prints "no record to show" rather than a number.

## Why the ledger is underneath all of it

The interesting claim is not "this wallet bought". It is **"we said so before it
went up"**, and without an append-only record that rests entirely on the
author's word.

Every call is written to `flyon.jsonl` at the block it was seen, hashed into a
chain. Settlement is a separate line that cannot replace the first one. A test
checks that no settlement was ever written before the call it settles.

Whoever holds the file can still rewrite it end to end — see the README — so the
head hash is printed in the page footer, to be pinned somewhere you do not
control.

## What would make this dishonest

Written down so it is obvious if it ever happens:

- settling from anything but the first trade after the window
- dropping calls that never settled, instead of showing them as open
- recomputing an old confidence once the outcome is known
- choosing the watched wallets by hand and calling it a system
- publishing the hit rate only when it is good
- putting a number on the page that is not in `data.json`

The last one is structural rather than a promise: the page contains no figure of
its own. Every number it shows is read at load time from the file the indexer
wrote, so there is no edit to the HTML that changes what the page claims.
