"""
Which chain, and what counts as money on it.

## The quote asset

To say a wallet is "up", you have to be up *in something*. FLYON measures profit
in the **quote asset of the pool the trade happened in** — the wrapped native
token, or a stablecoin — and never in dollars.

That is a deliberate limitation and it is the honest one. A dollar figure needs a
price feed, a price feed is somebody's API, and the moment a leaderboard depends
on a number nobody can re-derive, it is a leaderboard you have to take on faith.
Quote-asset profit comes out of the same swap logs as everything else, so a
stranger with an RPC endpoint can recompute every row.

If neither side of a pool is a known quote asset, trades in it are **counted but
not priced** — they appear in the feed and are excluded from profit, with the
reason shown. Guessing a route through two more pools to invent a price is how
you end up confidently ranking someone who never made a cent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Network:
    key: str
    name: str
    chain_id: int
    rpc: str
    explorer: str
    #: address → (symbol, decimals, rank). Lower rank wins when a pool has two.
    quotes: dict[str, tuple[str, int, int]] = field(default_factory=dict)

    def tx_url(self, tx: str) -> str:
        return f"{self.explorer.rstrip('/')}/tx/{tx}"

    def address_url(self, address: str) -> str:
        return f"{self.explorer.rstrip('/')}/address/{address}"


#: Robinhood Chain is a permissionless EVM L2 built on Arbitrum.
#:
#: The quote list starts empty on purpose. Filling it with addresses copied from
#: a forum is how a tracker ends up denominating profit in a token that is not
#: what it says it is — see `flyon quotes --add` and docs/PNL.md. An empty list
#: is not a broken tracker: every trade still appears, none is priced, and the
#: page says so in as many words.
NETWORKS: dict[str, Network] = {
    "robinhood": Network(
        key="robinhood",
        name="Robinhood Chain",
        chain_id=4663,
        rpc="https://rpc.mainnet.chain.robinhood.com",
        explorer="https://robinhoodchain.blockscout.com",
        quotes={},
    ),
    "robinhood-testnet": Network(
        key="robinhood-testnet",
        name="Robinhood Chain testnet",
        chain_id=46630,
        rpc="https://rpc.testnet.chain.robinhood.com",
        explorer="https://explorer.testnet.chain.robinhood.com",
        quotes={},
    ),
    #: Only for the offline demo. It is not a chain and says so.
    "demo": Network(
        key="demo",
        name="the demo chain (not real)",
        chain_id=1337,
        rpc="",
        explorer="https://example.invalid",
        quotes={"0x" + "11" * 20: ("WMOCK", 18, 0)},
    ),
}


def network(key: str) -> Network:
    net = NETWORKS.get(key)
    if net is None:
        raise KeyError(f"unknown network {key!r}. Known: {', '.join(NETWORKS)}")
    return net


def pick_quote(net: Network, token0: str, token1: str) -> tuple[int, str] | None:
    """
    Which side of this pool is the money, as (0 or 1, symbol).

    None when neither side is a known quote asset — the caller must then leave
    the trade unpriced rather than picking a side.
    """
    a, b = token0.lower(), token1.lower()
    ha, hb = net.quotes.get(a), net.quotes.get(b)
    if ha and hb:
        return (0, ha[0]) if ha[2] <= hb[2] else (1, hb[0])
    if ha:
        return 0, ha[0]
    if hb:
        return 1, hb[0]
    return None
