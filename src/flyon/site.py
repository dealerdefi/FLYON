"""
The page.

One rule decides how this file is written: **the page contains no number of its
own.** Every figure on it — every row of the board, every line of the feed, the
hit rate, the block range, the head hash — is read at load time out of one JSON
file that the indexer produced from chain logs.

So there is no edit anyone can make to the HTML that changes what the page
claims. To change a number you have to change the chain.

    flyon index …            reads blocks  → data.json
    flyon site               data.json     → index.html (the template, filled)

The template carries a single `/*__DATA__*/` marker and nothing else numeric. If
that marker is missing, this refuses to build rather than shipping a page that
renders whatever was baked into it last time.

## The demo band

A file with `"demo": true` describes an invented market. The page must say so,
so `build` checks the template still contains the band element and **raises if
it does not**. Deleting the banner from the HTML does not get you a clean-looking
demo; it gets you a build error, and a test makes sure of that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chains import Network
from .index import Scan, Trade
from .ledger import Verdict
from .pnl import Wallet, leaderboard
from .signals import Score, Signal

MARKER = "/*__DATA__*/"
BAND_ID = 'id="demo-band"'

#: Said on the page itself, not only in the docs. These are the reasons a row
#: here can be wrong even when every number in it is correct.
CAVEATS = [
    "Profit is measured in the pool's quote asset, never in dollars. A dollar "
    "figure would need a price feed you would have to take on trust.",
    "Realised profit is closed trades only, matched first-in-first-out. What a "
    "wallet still holds is shown as a quantity and never valued.",
    "Wallets holding tokens this chain never saw them buy are left off the "
    "board. Their proceeds are set aside and shown separately, because nothing "
    "here can tell an airdrop from a bridge from a second wallet.",
    "Gas and fees are not counted. They are not in a swap log, and estimating "
    "them would be inventing a number.",
    "Two wallets trading with each other can post any profit they like. No "
    "amount of reading a public chain can rule that out.",
    "None of this is advice, and none of it is a forecast. It is a record of "
    "what already happened, and a running score of how the record has held up.",
]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── the data file ────────────────────────────────────────────────────────────


def wallet_row(w: Wallet, net: Network, rank: int) -> dict:
    row = w.row()
    row["rank"] = rank
    row["url"] = net.address_url(w.address)
    row["positions"] = [p.row() for p in sorted(w.positions, key=lambda p: -p.realised)[:6]]
    return row


def trade_row(t: Trade, net: Network) -> dict:
    row = t.row()
    row["url"] = net.tx_url(t.tx)
    row["wallet_url"] = net.address_url(t.wallet) if t.wallet else None
    return row


def signal_row(s: Signal, net: Network) -> dict:
    row = s.row()
    row["url"] = net.tx_url(s.tx)
    row["wallet_url"] = net.address_url(s.wallet)
    return row


@dataclass
class Data:
    net: Network
    scan: Scan
    wallets: dict[str, Wallet]
    score: Score
    ledger: Verdict
    demo: bool = False
    feed_size: int = 60
    board_size: int = 25

    def build(self) -> dict[str, Any]:
        board = leaderboard(self.wallets, limit=self.board_size)
        set_aside = sorted(
            (w for w in self.wallets.values() if w.unknown_basis and w.quarantined > 0),
            key=lambda w: -w.quarantined,
        )[: self.board_size]

        feed = sorted(self.scan.trades, key=lambda t: (t.block, t.index), reverse=True)
        return {
            "generated_at": now(),
            "demo": self.demo,
            "network": {
                "key": self.net.key, "name": self.net.name,
                "chain_id": self.net.chain_id, "explorer": self.net.explorer,
            },
            "range": {"from": self.scan.from_block, "to": self.scan.to_block},
            "summary": self.scan.summary() | {
                "wallets": len(self.wallets),
                "on_board": len(board),
                "set_aside": len(set_aside),
                "quote": board[0].positions[0].quote if board and board[0].positions else "",
            },
            "leaderboard": [wallet_row(w, self.net, i + 1) for i, w in enumerate(board)],
            "set_aside": [wallet_row(w, self.net, 0) for w in set_aside],
            "feed": [trade_row(t, self.net) for t in feed[: self.feed_size]],
            # Settled calls lead, newest first, then what is still open. Showing
            # only the newest rows would fill the panel with "open" and quietly
            # hide every hit and miss — which is the one thing this panel is for.
            "signals": {
                "score": self.score.row(),
                "rows": [signal_row(s, self.net) for s in (
                    sorted(self.score.settled, key=lambda s: -(s.exit_block or 0))[:26]
                    + sorted(self.score.open, key=lambda s: -s.block)[:14]
                )],
            },
            "ledger": {"ok": self.ledger.ok, "lines": self.ledger.lines,
                       "head": self.ledger.head, "reason": self.ledger.reason},
            "caveats": CAVEATS,
        }


def write_data(data: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
    return p


# ── the page ─────────────────────────────────────────────────────────────────


class TemplateRefused(Exception):
    """The template cannot honestly render this data, and says which part."""


def render(template: str, data: dict[str, Any]) -> str:
    if MARKER not in template:
        raise TemplateRefused(
            f"the template has no {MARKER} marker, so the page would render "
            "whatever numbers were last baked into it. Refusing to build."
        )
    if data.get("demo") and BAND_ID not in template:
        raise TemplateRefused(
            "this data is from the demo chain and the template has no "
            f"{BAND_ID} element. A demo that does not say so is the one thing "
            "this project will not ship."
        )
    # The payload sits inside a <script type="application/json"> block, so the
    # one sequence that could end that block early is escaped. A token symbol is
    # attacker-controlled text on any public chain, and "</script>" is a
    # perfectly legal thing to name a token.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(MARKER, payload)


def build_site(data: dict[str, Any], template_path: str | Path,
               out_dir: str | Path) -> dict[str, Path]:
    """Write index.html and data.json side by side. Both are the deliverable."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    template = Path(template_path).read_text(encoding="utf-8")

    page = out / "index.html"
    page.write_text(render(template, data), encoding="utf-8")
    written = {"page": page, "data": write_data(data, out / "data.json")}

    # The fly travels with the page, so the site is one folder you can host.
    art = Path(template_path).parent / "fly.png"
    if art.exists():
        (out / "fly.png").write_bytes(art.read_bytes())
        written["art"] = out / "fly.png"
    return written
