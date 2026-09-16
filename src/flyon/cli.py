"""
The command line.

    flyon demo                     invent a market, index it, build the page
    flyon index --from N --to M    read real blocks
    flyon signals                  record calls and settle the ones that are due
    flyon site                     rebuild the page from what is stored
    flyon board · feed · score     the same numbers, printed
    flyon doctor                   endpoint, chain id, ledger, quote assets

Everything lands in one folder — `./flyon-data` unless `--home` or `$FLYON_HOME`
says otherwise:

    trades.json     what the indexer read, so nothing is re-fetched to redraw
    flyon.jsonl      the append-only record: scans, calls, settlements
    site/           index.html + data.json + fly.png, ready to host

Nothing here signs anything or spends anything. The RPC layer has six methods
and all six read.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from . import chains
from .index import Scan, Trade, scan
from .ledger import Ledger, verify
from .pnl import build as build_pnl, leaderboard
from .rpc import Chain, Http, Replay, RpcError, open_chain
from .signals import DEFAULT_WINDOW, Score, emit, settle_all
from .site import Data, build_site

VERSION = "0.1.0"

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_C = {"green": "\033[38;5;112m", "bright": "\033[38;5;155m", "dim": "\033[38;5;65m",
      "mute": "\033[38;5;244m", "gold": "\033[38;5;221m", "red": "\033[38;5;174m",
      "line": "\033[38;5;236m", "bold": "\033[1m", "off": "\033[0m"}


def p(text: str, style: str = "") -> str:
    return text if not (COLOR and style) else "".join(_C[s] for s in style.split()) + text + _C["off"]


def rule(w: int = 66) -> str:
    return p("─" * w, "line")


def head(text: str) -> None:
    print()
    print(p(text, "bright bold"))
    print(rule())


def die(msg: str, code: int = 1):
    print(p(f"✗ {msg}", "red"), file=sys.stderr)
    raise SystemExit(code)


def banner(args=None) -> None:
    """
    The mark, once, at the top.

    Skipped when output is not a terminal, so a piped or redirected run stays
    machine-readable, and when --no-banner says so.
    """
    if getattr(args, "no_banner", False) or not sys.stdout.isatty():
        return
    from .banner import render

    width = min(shutil.get_terminal_size((80, 24)).columns, 100)
    print("\n".join(render(width=width, colour=COLOR)))


# ── the folder ───────────────────────────────────────────────────────────────


class Home:
    def __init__(self, path: str | Path | None) -> None:
        raw = path or os.environ.get("FLYON_HOME") or "./flyon-data"
        self.root = Path(raw).expanduser().resolve()

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    @property
    def trades(self) -> Path:
        return self.root / "trades.json"

    @property
    def ledger(self) -> Path:
        return self.root / "flyon.jsonl"

    @property
    def site(self) -> Path:
        return self.root / "site"

    def load(self) -> tuple[list[Trade], dict]:
        if not self.trades.exists():
            die(f"nothing indexed yet in {self.root}\n"
                f"  flyon demo                     invent a market to look at\n"
                f"  flyon index --from N --to M    read real blocks")
        blob = json.loads(self.trades.read_text(encoding="utf-8"))
        rows = [Trade(**{k: v for k, v in r.items() if k != "side"}) for r in blob["trades"]]
        return rows, blob.get("meta", {})

    def save(self, sc: Scan, net_key: str, demo: bool) -> None:
        self.ensure()
        self.trades.write_text(json.dumps({
            "meta": {"network": net_key, "demo": demo, **sc.summary()},
            "trades": [t.row() for t in sc.trades],
        }, indent=0), encoding="utf-8")


def template_path() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here.parent.parent / "site", here / "site"):
        if (candidate / "index.template.html").exists():
            return candidate / "index.template.html"
    die("cannot find site/index.template.html next to the package")
    raise SystemExit(1)


# ── shared work ──────────────────────────────────────────────────────────────


def rebuild(home: Home, net: chains.Network, trades: list[Trade], meta: dict,
            window: int, watch: int, quiet: bool = False) -> dict:
    """Score the calls, write the ledger lines, build the page. One path only."""
    sc = Scan(trades=trades,
              from_block=meta.get("from_block", 0), to_block=meta.get("to_block", 0),
              swaps_seen=meta.get("swaps_seen", len(trades)),
              unattributed=meta.get("unattributed", 0))
    wallets = build_pnl(trades)
    board = leaderboard(wallets, limit=watch)
    watched = {w.address for w in board}

    signals = emit(trades, watched, window=window)
    settle_all(signals, [t for t in trades if t.priced and t.wallet], window=window)
    score = Score(signals=signals)

    led = Ledger(home.ledger)
    led.append("scan", **sc.summary(), network=net.key, demo=bool(meta.get("demo")))
    seen = {e.body.get("id") for e in led.of("signal")}
    settled_ids = {e.body.get("id") for e in led.of("settled")}
    for s in signals:
        if s.id not in seen:
            led.append("signal", **{k: v for k, v in s.row().items()
                                    if k in ("id", "wallet", "token", "pool",
                                             "block", "tx", "entry", "size", "confidence")})
        if s.settled and s.id not in settled_ids:
            led.append("settled", id=s.id, exit=s.exit, exit_block=s.exit_block,
                       change=s.change, outcome=s.outcome, reason=s.reason)

    data = Data(net=net, scan=sc, wallets=wallets, score=score,
                ledger=verify(home.ledger), demo=bool(meta.get("demo"))).build()
    written = build_site(data, template_path(), home.site)

    if not quiet:
        head("built")
        print(f"  page     {written['page']}")
        print(f"  data     {written['data']}")
        print(f"  ledger   {home.ledger}  ({len(led)} lines)")
        print()
        print("  " + p(score.headline(), "gold" if score.settled else "mute"))
    return data


# ── verbs ────────────────────────────────────────────────────────────────────


def cmd_demo(args) -> int:
    from .demo import DemoChain, demo_network

    home = Home(args.home)
    net = demo_network()
    chain_demo = DemoChain(seed=args.seed, blocks=args.blocks)
    ch = Chain(call=chain_demo, expect_chain_id=net.chain_id)

    banner(args)
    print()
    print(p("  an invented market, read by the real indexer", "bright bold"))
    print(p(f"  seed {args.seed} · nothing here is a real chain", "mute"))

    sc = scan(ch, net, chain_demo.start_block, chain_demo.head_block, step=5000)
    home.save(sc, net.key, demo=True)
    if args.tape:
        chain_demo.save_tape(args.tape)
        print(p(f"  tape written to {args.tape}", "dim"))

    rebuild(home, net, sc.trades, home.load()[1], args.window, args.watch)
    print()
    print(p("  open it:", "mute"), home.site / "index.html")
    return 0


def cmd_index(args) -> int:
    home = Home(args.home)
    net = chains.network(args.network)
    if args.quote:
        for spec in args.quote:
            addr, sym, dec = spec.split(":")
            net.quotes[addr.lower()] = (sym, int(dec), 0)

    try:
        ch = open_chain(net.rpc, expect=net.chain_id, replay=args.replay)
        to_block = args.to if args.to is not None else ch.head()
        from_block = args.from_ if args.from_ is not None else max(0, to_block - args.blocks + 1)
        sc = scan(ch, net, from_block, to_block, step=args.step)
    except RpcError as e:
        die(f"{e}\n"
            f"  the endpoint for {net.key} is {net.rpc}\n"
            f"  if it is unreachable from here, index elsewhere and use --replay")

    if args.record and isinstance(ch.call, Http):
        Path(args.record).write_text(json.dumps(ch.call.tape, sort_keys=True), encoding="utf-8")
        print(p(f"recorded {len(ch.call.tape)} responses to {args.record}", "dim"))

    home.save(sc, net.key, demo=False)
    head(f"read blocks {sc.from_block:,} → {sc.to_block:,}")
    for k, v in sc.summary().items():
        print(f"  {k.replace('_', ' '):<18} {v:,}" if isinstance(v, int) else f"  {k:<18} {v}")
    if not net.quotes:
        print()
        print(p("  no quote assets registered for this network, so nothing is priced.", "gold"))
        print(p("  add one with --quote 0xADDRESS:SYMBOL:DECIMALS  (see docs/PNL.md)", "mute"))

    rebuild(home, net, sc.trades, home.load()[1], args.window, args.watch)
    return 0


def cmd_site(args) -> int:
    home = Home(args.home)
    trades, meta = home.load()
    rebuild(home, chains.network(meta.get("network", "demo")), trades, meta,
            args.window, args.watch)
    return 0


def cmd_board(args) -> int:
    home = Home(args.home)
    trades, meta = home.load()
    wallets = build_pnl(trades)
    rows = leaderboard(wallets, limit=args.limit, include_unknown_basis=args.all)

    head(f"top {len(rows)} by realised profit" + ("  (unaccounted wallets included)" if args.all else ""))
    if not rows:
        print(p("  nothing has closed a trade yet.", "mute"))
        return 0
    print(p(f"  {'#':<3} {'wallet':<44} {'realised':>12} {'spent':>10} {'roi':>8} {'trades':>7}", "mute"))
    for i, w in enumerate(rows, 1):
        flag = p(" ⚑", "gold") if w.unknown_basis else ""
        roi = "—" if w.roi is None else f"{w.roi * 100:+.1f}%"
        print(f"  {i:<3} {w.address:<44} "
              f"{p(f'{w.realised:+12.3f}', 'green' if w.realised > 0 else 'red')} "
              f"{w.spent:10.2f} {roi:>8} {w.trades:7d}{flag}")

    aside = [w for w in wallets.values() if w.unknown_basis]
    if aside and not args.all:
        print()
        print(p(f"  {len(aside)} wallets left off: they sold tokens this chain never saw them buy.", "gold"))
        print(p(f"  {sum(w.quarantined for w in aside):,.2f} of proceeds set aside. flyon board --all to see them.", "mute"))
    return 0


def cmd_feed(args) -> int:
    home = Home(args.home)
    trades, _meta = home.load()
    rows = sorted(trades, key=lambda t: (t.block, t.index), reverse=True)[: args.limit]
    head(f"last {len(rows)} trades")
    for t in rows:
        mark = p("BUY ", "green") if t.side == "buy" else p("SELL", "red")
        who = t.wallet[:16] + "…" if t.wallet else p("unattributed", "mute")
        flag = "" if t.priced else p("  unpriced", "gold")
        print(f"  {t.block:>10,}  {mark}  {abs(t.base_amount):>14,.0f} {t.base_symbol:<9}"
              f" {abs(t.quote_amount):>10,.4f} {t.quote_symbol:<7} {who}{flag}")
    return 0


def cmd_score(args) -> int:
    home = Home(args.home)
    trades, _meta = home.load()
    wallets = build_pnl(trades)
    watched = {w.address for w in leaderboard(wallets, limit=args.watch)}
    signals = emit(trades, watched, window=args.window)
    settle_all(signals, [t for t in trades if t.priced and t.wallet], window=args.window)
    score = Score(signals=signals)

    head("the record")
    r = score.row()
    if not score.settled:
        print(p("  no call has settled yet — nothing to claim, and nothing claimed.", "mute"))
        print(f"  {len(score.open)} open")
        return 0
    hit, said, gap = r["hit_rate"], r["said"], r["gap"]
    verdict = "flattering itself" if gap > 0 else "harder on itself than it needs to be"
    print(f"  settled    {r['settled']}   open {r['open']}")
    print("  hit rate   " + p(f"{hit * 100:.0f}%", "bright"))
    print(f"  said       {said * 100:.0f}%      the wallets' own prior hit rate")
    print("  gap        " + p(f"{gap * 100:+.0f} pts", "gold" if abs(gap) > 0.05 else "green")
          + f"   {verdict}")
    print(f"  brier      {r['brier']:.3f}    " + p("0 perfect · 0.25 a coin · 1 certain and wrong", "mute"))
    print(f"  median     {r['median_change'] * 100:+.1f}%   price move over the window")
    return 0


def cmd_doctor(args) -> int:
    home = Home(args.home)
    net = chains.network(args.network)
    problems = 0

    banner(args)
    print()
    print(p(f"  flyon {VERSION}", "bright bold"))
    print(p(f"  {home.root}", "mute"))

    head("endpoint")
    if not net.rpc:
        print(f"  {p('·', 'dim')} {net.key} has no endpoint — it is the offline demo")
    else:
        try:
            ch = open_chain(net.rpc, expect=None)
            live = ch.chain_id()
            ok = live == net.chain_id
            problems += 0 if ok else 1
            print(f"  {p('✓' if ok else '✗', 'green' if ok else 'red')} {net.rpc}")
            print(p(f"      reports chain {live}, expected {net.chain_id}", "mute"))
            if ok:
                print(p(f"      head block {ch.head():,}", "mute"))
        except RpcError as e:
            problems += 1
            print(f"  {p('✗', 'red')} {net.rpc}")
            print(p(f"      {e}", "mute"))
            print(p("      unreachable is not the same as wrong — index from a machine", "dim"))
            print(p("      that can reach it and bring the result back with --record/--replay", "dim"))

    head("quote assets")
    if net.quotes:
        for addr, (sym, dec, rank) in net.quotes.items():
            print(f"  {p('✓', 'green')} {sym:<8} {addr}  {dec} decimals  rank {rank}")
    else:
        print(f"  {p('!', 'gold')} none registered — every trade will be read and none priced")
        print(p("      that is a safe default, not a bug. see docs/PNL.md", "mute"))

    head("ledger")
    v = verify(home.ledger)
    if v.ok:
        print(f"  {p('✓', 'green')} {v.lines} records, unbroken")
        print(p(f"      head {v.head[:24]}…", "dim"))
    else:
        problems += 1
        print(f"  {p('✗', 'red')} broken at record {v.broke_at}: {v.reason}")

    head("capability")
    from . import rpc as rpcmod
    print(f"  {p('✓', 'green')} {len(rpcmod.READ_METHODS)} rpc methods, all of them reads")
    print(p("      no signing, no keys, no wallet — flyon cannot move anything", "mute"))

    print()
    print(rule())
    print(p(f"  {problems} things want you.", "gold") if problems
          else p("  nothing is wrong.", "green bold"))
    return 0


# ── wiring ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="flyon",
        description="A wallet tracker that keeps score of its own calls.",
        epilog="flyon demo  ·  flyon index --from N --to M  ·  flyon doctor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--home", help="where to keep things. Or $FLYON_HOME, or ./flyon-data")
    ap.add_argument("--version", action="version", version=f"flyon {VERSION}")
    ap.add_argument("--no-banner", action="store_true", help="skip the mark")
    sub = ap.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help="blocks a call gets before it is settled")
        sp.add_argument("--watch", type=int, default=12,
                        help="how many wallets off the top of the board to record calls for")

    d = sub.add_parser("demo", help="invent a market and read it with the real indexer")
    d.add_argument("--seed", type=int, default=7331)
    d.add_argument("--blocks", type=int, default=40_000,
                   help="how many blocks of invented market to generate")
    d.add_argument("--tape", help="also write the rpc responses, for --replay")
    common(d)
    d.set_defaults(fn=cmd_demo)

    i = sub.add_parser("index", help="read real blocks")
    i.add_argument("--network", default="robinhood", choices=list(chains.NETWORKS))
    i.add_argument("--from", dest="from_", type=int, help="first block")
    i.add_argument("--to", type=int, help="last block (default: the head)")
    i.add_argument("--blocks", type=int, default=20_000, help="how far back, if --from is absent")
    i.add_argument("--step", type=int, default=2_000, help="blocks per getLogs call")
    i.add_argument("--quote", nargs="*", help="0xADDRESS:SYMBOL:DECIMALS — what counts as money")
    i.add_argument("--record", help="write every rpc response to this file")
    i.add_argument("--replay", help="read from a recorded file instead of the network")
    common(i)
    i.set_defaults(fn=cmd_index)

    s = sub.add_parser("site", help="rebuild the page from what is already stored")
    common(s)
    s.set_defaults(fn=cmd_site)

    b = sub.add_parser("board", help="top by realised profit")
    b.add_argument("--limit", type=int, default=20)
    b.add_argument("--all", action="store_true", help="include wallets with unaccounted tokens")
    b.set_defaults(fn=cmd_board)

    f = sub.add_parser("feed", help="the most recent trades")
    f.add_argument("--limit", type=int, default=25)
    f.set_defaults(fn=cmd_feed)

    sc = sub.add_parser("score", help="how the calls have actually done")
    common(sc)
    sc.set_defaults(fn=cmd_score)

    doc = sub.add_parser("doctor", help="endpoint, chain id, quote assets, ledger")
    doc.add_argument("--network", default="robinhood", choices=list(chains.NETWORKS))
    doc.set_defaults(fn=cmd_doctor)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "cmd", None):
        banner(args)
        print()
        ap.print_help()
        return 0
    try:
        return args.fn(args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
