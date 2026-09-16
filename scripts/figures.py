#!/usr/bin/env python3
"""
Regenerate every number the README quotes.

    python scripts/figures.py            print them
    python scripts/figures.py --check    fail if the README disagrees

Nothing on the front page is typed by hand. Each figure comes out of a demo
market built from a fixed seed, read by the real indexer, with no network
involved — so anybody can run this and get the same numbers, and a change to the
scoring shows up as a diff rather than as a quietly nicer README.

`--check` is what turns that from a habit into a promise: it reads README.md and
exits non-zero if the page says something the code no longer produces.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flyon.demo import DemoChain, demo_network  # noqa: E402
from flyon.index import scan  # noqa: E402
from flyon.pnl import build, leaderboard  # noqa: E402
from flyon.rpc import Chain  # noqa: E402
from flyon.signals import Score, emit, settle_all  # noqa: E402

SEED = 7331
WINDOW = 7_200
WATCH = 12


def figures() -> dict[str, str]:
    net = demo_network()
    d = DemoChain(seed=SEED)
    sc = scan(Chain(call=d, expect_chain_id=net.chain_id), net,
              d.start_block, d.head_block, step=5_000)

    wallets = build(sc.trades)
    board = leaderboard(wallets, limit=25)
    aside = [w for w in wallets.values() if w.unknown_basis]

    priced = [t for t in sc.trades if t.priced and t.wallet]
    sigs = emit(sc.trades, {w.address for w in leaderboard(wallets, limit=WATCH)}, window=WINDOW)
    settle_all(sigs, priced, window=WINDOW)
    score = Score(signals=sigs)

    top = board[0]
    return {
        "trades": f"{len(sc.trades):,}",
        "swaps": f"{sc.swaps_seen:,}",
        "wallets": f"{len(wallets):,}",
        "on_board": str(len(board)),
        "set_aside": str(len(aside)),
        "quarantined": f"{sum(w.quarantined for w in aside):,.2f}",
        "top_realised": f"{top.realised:+.3f}",
        "top_roi": f"{top.roi * 100:+.1f}%",
        "settled": str(len(score.settled)),
        "open": str(len(score.open)),
        "hit_rate": f"{score.hit_rate * 100:.0f}%",
        "hit_exact": f"{score.hit_rate * 100:.1f}%",
        "said_exact": f"{score.said * 100:.1f}%",
        "said": f"{score.said * 100:.0f}%",
        "gap": f"{score.gap * 100:+.0f}",
        "brier": f"{score.brier:.3f}",
        "median": f"{score.median_change * 100:+.1f}%",
        "headline": score.headline(),
    }


def expectations(f: dict[str, str]) -> dict[str, str]:
    return {
        "the score headline": f["headline"],
        "trades read": f"{f['trades']} trades",
        "wallets": f"{f['wallets']} wallets",
        "set aside": f"{f['quarantined']} WMOCK set aside",
        "the hit rate": f"hit {f['hit_rate']}",
        "the brier score": f"brier {f['brier']}",
        "the median move": f"median {f['median']}",
        "the scoreboard block's hit rate": f"hit rate   {f['hit_exact']}",
        "the scoreboard block's said": f"said       {f['said_exact']}",
        "the scoreboard block's brier": f"brier     {f['brier']}",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if README.md quotes a number this no longer produces")
    args = ap.parse_args()

    f = figures()
    width = max(len(k) for k in f)
    for k, v in f.items():
        print(f"{k.ljust(width)}  {v}")

    if not args.check:
        return 0

    readme = ROOT / "README.md"
    if not readme.exists():
        print("\nno README.md to check against")
        return 1
    text = readme.read_text(encoding="utf-8")
    wrong = [(n, w) for n, w in expectations(f).items() if w not in text]

    print()
    if wrong:
        for name, want in wrong:
            print(f"✗ README does not say {name}: {want!r}")
        return 1
    print(f"✓ README agrees with the code on all {len(expectations(f))} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
