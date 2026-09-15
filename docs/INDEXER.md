# Logs to trades

```
eth_getLogs ─→ decode ─→ ask each pool its tokens ─→ flip the sign ─→ Trade
```

That is the whole indexer. It is careful and boring on purpose: everything
downstream is arithmetic over its rows.

## The sign

A `Swap` log reports amounts **from the pool's side**. Positive means the token
went into the pool, so the trader gave it up.

```
amount0 = -1,000,000   PEPO out of the pool   → the trader received PEPO
amount1 = +2 × 10¹⁸    WETH into the pool     → the trader paid WETH
                                              → a BUY
```

One minus sign apart from reading every buy as a sell. The feed would go green
where it should go red, the leaderboard would invert, and nothing about the page
would look broken — so `test_index.py` checks it from both directions, in both
pool layouts, for both AMM versions.

Uniswap-v2-style pools report four unsigned numbers instead; they are folded
into the same signed pair on the way in, and a test asserts both shapes describe
an identical trade.

## Three things it refuses to guess

### The trader

A swap log names the pool's caller and recipient. On a router trade both are the
router, not a person. So the wallet credited is the recipient, unless the
recipient is a known router, in which case it is the sender.

When neither can be established the trade is recorded with `wallet: null`,
appears in the feed marked `unattributed`, and is left out of the leaderboard.
Crediting a router would put a contract at the top of the board, which is a
reliable tell of a tracker nobody checked.

### The price

Only pools with a registered quote asset are priced. The rest are carried
through unpriced — visible, counted, absent from profit. See [PNL.md](PNL.md).

### The decimals

Read from the token itself with `decimals()`. A contract that answers something
implausible, or nothing at all, leaves the amount in raw units with a flag.

Assuming 18 on a 6-decimal token is a factor of a trillion, which on a
leaderboard looks like a genius.

## Pools are asked once

A pool's tokens never change, so each is queried once per run and cached, along
with each token's symbol and decimals. On a busy range that is the difference
between four calls and four thousand. A test asserts the second lookup costs
nothing.

A pool that emits a `Swap` but will not answer `token0()`/`token1()` is recorded
in `unreadable_pools` and skipped. Inventing its pair would poison everything
downstream, and the count is printed in the page footer so it is never silently
zero.

## Reading a range

`eth_getLogs` is refused above a few thousand blocks by most endpoints, so the
range is walked in steps — 2,000 by default, `--step` to change it. The step
size changes the number of calls and nothing else; there is a test that runs the
same range at 1,000 and 40,000 and compares the trades byte for byte.

Trades come back sorted by `(block, logIndex)` — chain order, not arrival order —
because FIFO accounting downstream depends on it.

## Record and replay

```bash
flyon index --from N --to M --record run.json     # keep every response
flyon index --replay run.json                     # the same blocks, forever
```

A replay opens no socket and **raises on a call that was never recorded** rather
than returning something plausible. A replay that quietly invents an answer is
worse than no replay, because the number it produces looks exactly like a real
one.

Three uses:

- the test suite runs offline, so no test depends on a public endpoint being up
- a bug from last week can be reproduced exactly
- somebody who does not trust your server can re-derive every row on the page

## It only reads

`rpc.py` has six methods:

```
eth_chainId  eth_blockNumber  eth_getLogs
eth_getBlockByNumber  eth_call  eth_getTransactionReceipt
```

All six read. There is no `eth_sendRawTransaction`, no signing, no key handling,
no wallet — and a test parses the file to prove those words do not appear in it.
Both transports carry the same refusal, including the demo one, because a fake
chain that would accept a write is a hole in the guarantee even when nothing is
on the other end of it.

Before any read, `eth_chainId` is checked against the chain the caller asked
for, and a mismatch raises. An endpoint quietly pointed at a different network
is the kind of thing that produces a complete, plausible, entirely wrong page.
