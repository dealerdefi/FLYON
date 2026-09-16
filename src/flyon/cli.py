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
from . import ui
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
    W = ui.width()

    quote = next((pos.quote for w in rows for pos in w.positions), "")
    note = f"{len(rows)} of {len(wallets)} wallets" + (" · unaccounted included" if args.all else "")
    for line in ui.head("top by realised profit", note, W):
        print(line)

    if not rows:
        print(ui.c("  nothing has closed a trade yet.", "mute"))
        return 0

    scale = max(abs(w.realised) for w in rows) or 1.0

    # The address is printed in full and never truncated — a shortened one is
    # not something anybody can paste into an explorer. Everything else gives
    # way to it, and the flag sits beside it so it is never the part that is cut.
    addr_w = 42
    rest = ui.flex(ui.inner(W), [3, addr_w, 2, 11, 8], gap=1, floor=12)
    barw = max(6, int(rest * 0.55))
    recw = max(5, rest - barw - 1)
    spec = [(3, ">"), (addr_w, "<"), (2, "<"), (11, ">"), (barw, "<"),
            (8, ">"), (recw, "<")]

    body = [ui.columns([[
        ui.c("#", "mute"), ui.c("wallet", "mute"), "", ui.c("realised", "mute"),
        ui.c(f"±{scale:.2f} {quote}"[: barw], "line"), ui.c("roi", "mute"),
        ui.c("record", "mute"),
    ]], spec, gap=1)[0], ""]

    for i, w in enumerate(rows, 1):
        # A token counts once it has been sold into, whether or not the bag is
        # empty — realised profit is realised whether the position is closed or
        # merely trimmed. Positions never sold have no verdict and are left out.
        won = [pos.realised > 0 for pos in sorted(w.positions, key=lambda x: x.last_block)
               if pos.sells]
        body.extend(ui.columns([[
            ui.c(f"{i}", "lime" if i <= 3 else "mute"),
            ui.c(w.address, "bright" if i <= 3 else ""),
            ui.c("⚑", "gold") if w.unknown_basis else "",
            ui.c(f"{w.realised:+.3f}", "green" if w.realised > 0 else "rose"),
            ui.diverging(w.realised, scale, barw),
            ui.c("—" if w.roi is None else f"{w.roi * 100:+.1f}%",
                 "green" if (w.roi or 0) > 0 else "rose"),
            ui.strip(won, cap=recw),
        ]], spec, gap=1))

    for line in ui.frame(body, W,
                         foot="▲ a token sold into profit   ▾ sold at a loss   ⚑ tokens nobody saw bought"):
        print(line)

    aside = [w for w in wallets.values() if w.unknown_basis]
    if aside and not args.all:
        held = sum(w.quarantined for w in aside)
        print()
        print("  " + ui.c("⚑", "gold") + ui.c(
            f"  {len(aside)} wallet{'s' if len(aside) > 1 else ''} left off — "
            f"sold tokens this chain never saw them buy", "gold"))
        print("  " + ui.c(f"   {held:,.2f} {quote} of proceeds set aside, "
                          f"not counted as anyone's profit", "mute"))
        print("  " + ui.c("   flyon board --all shows them, flagged", "dim"))
    return 0


def cmd_feed(args) -> int:
    home = Home(args.home)
    trades, _meta = home.load()
    rows = sorted(trades, key=lambda t: (t.block, t.index), reverse=True)[: args.limit]
    W = ui.width()

    buys = sum(1 for t in rows if t.side == "buy")
    for line in ui.head("the last trades", f"{buys} bought · {len(rows) - buys} sold", W):
        print(line)
    if not rows:
        print(ui.c("  nothing in range.", "mute"))
        return 0

    scale = max(abs(t.quote_amount) for t in rows) or 1.0
    barw = min(20, ui.flex(ui.inner(W), [11, 5, 14, 10, 12, 20], gap=2, floor=8))
    spec = [(11, ">"), (5, "<"), (barw, "<"), (14, ">"), (10, "<"), (12, ">"), (20, "<")]

    body = []
    for t in rows:
        ink = "green" if t.side == "buy" else "rose"
        body.extend(ui.columns([[
            ui.c(f"{t.block:,}", "dim"),
            ui.c(t.side.upper(), ink, bold=True),
            ui.bar(abs(t.quote_amount), scale, barw, ink),
            ui.c(f"{abs(t.base_amount):,.0f}", "bright"),
            ui.c(t.base_symbol[:10], "mute"),
            ui.c(f"{abs(t.quote_amount):,.4f}", ""),
            ui.c(t.quote_symbol[:6], "mute") + "  " + (
                ui.c(t.wallet[:10] + "…", "dim") if t.wallet
                else ui.c("unattributed", "amber")),
        ]], spec))

    for line in ui.frame(body, W, foot=f"bar to {scale:,.2f} of quote moved"):
        print(line)
    return 0


def cmd_score(args) -> int:
    home = Home(args.home)
    trades, _meta = home.load()
    wallets = build_pnl(trades)
    watched = {w.address for w in leaderboard(wallets, limit=args.watch)}
    signals = emit(trades, watched, window=args.window)
    settle_all(signals, [t for t in trades if t.priced and t.wallet], window=args.window)
    score = Score(signals=signals)
    W = ui.width()

    r = score.row()
    for line in ui.head("the record", f"window {args.window:,} blocks", W):
        print(line)

    if not score.settled:
        for line in ui.frame([
            ui.c("no call has settled yet.", "bright"),
            "",
            ui.c("nothing is claimed, so there is nothing to show. "
                 f"{len(score.open)} open.", "mute"),
        ], W):
            print(line)
        return 0

    # Formatted from the score itself, never from row()'s rounded copy: a
    # number rounded to four places and then printed to three comes out one
    # digit away from the same number printed straight, and then the terminal
    # and the README quietly disagree about what the code said.
    hit, said, gap, brier = score.hit_rate, score.said, score.gap, score.brier
    median = score.median_change
    barw = max(12, min(30, ui.inner(W) - 62))

    body = [
        ui.kv("settled", ui.c(f"{r['settled']}", "bright", bold=True),
              f"{r['open']} still open", 11),
        "",
        ui.kv("hit rate", ui.c(f"{hit * 100:5.1f}%", "bright", bold=True)
              + "  " + ui.bar(hit, 1.0, barw, "green"),
              "of settled calls the price was higher", 11),
        ui.kv("said", ui.c(f"{said * 100:5.1f}%", "") + "  "
              + ui.bar(said, 1.0, barw, "deep"),
              "the wallets' own record going in", 11),
        "",
        ui.kv("gap", ui.c(f"{gap * 100:+5.0f} pts", "gold" if abs(gap) > 0.05 else "green",
                          bold=True),
              "flattering itself" if gap > 0 else "harder on itself than it needs to be", 11),
        ui.kv("brier", ui.c(f"{brier:.3f}", "bright")
              + "     " + ui.bar(min(brier, 0.5), 0.5, barw, "amber"),
              "0 perfect · 0.25 a coin · 1 certain and wrong", 11),
        ui.kv("median", ui.c(f"{median * 100:+.1f}%",
                             "green" if median > 0 else "rose"),
              "price move over the window", 11),
    ]
    for line in ui.frame(body, W, title=ui.c("scoreboard", "bright", bold=True)):
        print(line)

    rows = score.buckets()
    if rows:
        for line in ui.head("does the confidence mean anything",
                            "settled calls, grouped by what they said", W):
            print(line)
        errw = 7
        barw2 = ui.flex(ui.inner(W), [13, 7, 8, 8, errw], gap=2, floor=10)
        spec = [(13, "<"), (7, ">"), (8, ">"), (8, ">"), (barw2, "<"), (errw, ">")]
        table = [ui.columns([[ui.c(x, "mute") for x in
                              ("confidence", "calls", "said", "hit", "", "error")]], spec)[0], ""]
        for b in rows:
            err = b["hit"] - b["said"]
            table.extend(ui.columns([[
                ui.c(f"{b['low'] * 100:.0f}–{min(b['high'], 1.0) * 100:.0f}%", "dim"),
                ui.c(f"{b['n']}", ""),
                ui.c(f"{b['said'] * 100:.0f}%", "mute"),
                ui.c(f"{b['hit'] * 100:.0f}%", "bright"),
                ui.diverging(err, 0.5, spec[4][0]),
                ui.c(f"{err * 100:+.0f}", "green" if abs(err) < 0.1 else "rose"),
            ]], spec))
        for line in ui.frame(table, W,
                             foot="bar right of centre: did better than it said"):
            print(line)

    last = [s.outcome for s in sorted(score.settled, key=lambda s: s.exit_block or 0)]
    print()
    print("  " + ui.c("most recent", "mute") + "  " + ui.strip([bool(x) for x in last], cap=48))
    return 0


def cmd_doctor(args) -> int:
    home = Home(args.home)
    net = chains.network(args.network)
    problems = 0
    W = ui.width()

    banner(args)
    print()
    print("  " + ui.c(f"flyon {VERSION}", "bright", bold=True) + "   "
          + ui.c(str(home.root), "mute"))

    rows: list[str] = []

    def ok(text: str, note: str = "") -> None:
        rows.append(ui.c("✓", "green") + "  " + ui.pad(text, 46)
                    + ui.c(note, "mute"))

    def warn(text: str, note: str = "") -> None:
        rows.append(ui.c("!", "gold") + "  " + ui.pad(text, 46) + ui.c(note, "mute"))

    def bad(text: str, note: str = "") -> None:
        rows.append(ui.c("✗", "rose") + "  " + ui.pad(text, 46) + ui.c(note, "mute"))

    def note(text: str) -> None:
        rows.append(ui.c("·", "dim") + "  " + ui.c(text, "mute"))

    # ── endpoint ──
    rows.append(ui.c("ENDPOINT", "bright", bold=True))
    if not net.rpc:
        note(f"{net.key} has no endpoint — it is the offline demo")
    else:
        try:
            ch = open_chain(net.rpc, expect=None)
            live = ch.chain_id()
            good = live == net.chain_id
            (ok if good else bad)(net.rpc, f"reports chain {live}, expected {net.chain_id}")
            problems += 0 if good else 1
            if good:
                note(f"head block {ch.head():,}")
        except RpcError as e:
            problems += 1
            bad(net.rpc, str(e)[:52])
            note("unreachable is not the same as wrong — index where it answers,")
            note("then bring the result back with --record / --replay")

    # ── quote assets ──
    rows.extend(["", ui.c("QUOTE ASSETS", "bright", bold=True)])
    if net.quotes:
        for addr, (sym, dec, rank) in net.quotes.items():
            ok(f"{sym:<8} {addr}", f"{dec} decimals · rank {rank}")
    else:
        warn("none registered", "every trade is read, none is priced")
        note("that is a safe default, not a bug — see docs/PNL.md")

    # ── ledger ──
    rows.extend(["", ui.c("LEDGER", "bright", bold=True)])
    v = verify(home.ledger)
    if v.ok:
        ok(f"{v.lines:,} records, unbroken", v.head[:24] + "…" if v.lines else "")
    else:
        problems += 1
        bad(f"broken at record {v.broke_at}", str(v.reason)[:52])

    # ── capability ──
    from . import rpc as rpcmod

    rows.extend(["", ui.c("CAPABILITY", "bright", bold=True)])
    ok(f"{len(rpcmod.READ_METHODS)} rpc methods, all of them reads",
       "no signing · no keys · no wallet")
    note("flyon cannot move anything, and a test reads the file to prove it")

    for line in ui.frame(rows, W, ink="line"):
        print(line)

    print()
    if problems:
        print("  " + ui.c(f"⚑  {problems} thing{'s' if problems > 1 else ''} want you.", "gold"))
    else:
        print("  " + ui.c("✓  nothing is wrong.", "green", bold=True))
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
