#!/usr/bin/env python3
"""
Photograph the terminal — the real one.

    python scripts/terminal.py            write assets/terminal-*.png
    python scripts/terminal.py --text     print the screens instead

A pseudo terminal is opened, `flyon` is run inside it, and the bytes that come
back are replayed through a terminal emulator — colours and all — to get the
exact grid of characters a person would see. That grid is then drawn as a
picture.

Nothing here is a mock-up. If the banner is misaligned in the README, it is
misaligned in your shell.

Development-only: needs `pyte` to read the terminal and Playwright's Chromium to
draw. FLYON itself needs neither.
"""

from __future__ import annotations

import argparse
import codecs
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLS, LINES = 96, 58

#: (filename, argv, what it shows)
SHOTS = [
    ("terminal-doctor", ["doctor", "--network", "demo"], "the mark, and what it can reach"),
    ("terminal-board", ["board", "--limit", "8"], "top by realised profit"),
    ("terminal-score", ["score", "--window", "7200"], "how the calls actually did"),
]

DEFAULT_FG = "b9c9ae"
DEFAULT_BG = "050a05"

Row = list  # [(text, fg, bg, bold)]


def capture(argv: list[str], home: Path) -> list[Row]:
    import pty

    import pyte

    env = dict(os.environ, TERM="xterm-256color", FLYON_HOME=str(home),
               PYTHONPATH=str(ROOT / "src"), LINES=str(LINES), COLUMNS=str(COLS))
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, "-m", "flyon", *argv], env)

    screen = pyte.Screen(COLS, LINES)
    stream = pyte.Stream(screen)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    deadline = time.time() + 60
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        stream.feed(decoder.decode(chunk))
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    os.close(fd)

    out: list[Row] = []
    for y in range(screen.lines):
        line = screen.buffer[y]
        runs: Row = []
        for x in range(screen.columns):
            ch = line[x]
            key = (ch.fg, ch.bg, ch.bold)
            if runs and (runs[-1][1], runs[-1][2], runs[-1][3]) == key:
                runs[-1] = (runs[-1][0] + ch.data, *key)
            else:
                runs.append((ch.data, *key))
        while runs and not runs[-1][0].strip() and runs[-1][2] == "default":
            runs.pop()
        out.append(runs)
    while out and not any(r[0].strip() for r in out[-1]):
        out.pop()
    return out


def as_text(rows: list[Row]) -> str:
    return "\n".join("".join(r[0] for r in row).rstrip() for row in rows)


PAGE = """<!doctype html><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box }}
  body {{ background:#020502; display:flex; align-items:center; justify-content:center;
         padding:26px; font-family:"DejaVu Sans Mono",Menlo,Consolas,monospace }}
  .term {{ background:#{bg}; border:1px solid #17351c; border-radius:9px;
          box-shadow:0 0 0 1px #0a1a0c, 0 18px 60px rgba(0,0,0,.6); overflow:hidden }}
  .bar {{ height:27px; background:#0a1a0d; border-bottom:1px solid #16301a;
         display:flex; align-items:center; gap:7px; padding:0 11px }}
  .dot {{ width:9px; height:9px; border-radius:50%; background:#2d4030 }}
  .t {{ margin-left:9px; color:#41724a; font-size:11px; letter-spacing:.6px }}
  pre {{ color:#{fg}; font-size:{size}px; line-height:1.3; padding:14px 16px; white-space:pre }}
  b {{ font-weight:700 }}
</style>
<div class="term">
  <div class="bar"><i class="dot"></i><i class="dot"></i><i class="dot"></i>
    <span class="t">flyon</span></div>
  <pre>{body}</pre>
</div>
"""


def to_html(rows: list[Row]) -> str:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = []
    for row in rows:
        parts = []
        for text, fg, bg, bold in row:
            style = []
            if fg != "default":
                style.append(f"color:#{fg}")
            if bg != "default":
                style.append(f"background:#{bg}")
            piece = esc(text)
            if bold:
                piece = f"<b>{piece}</b>"
            parts.append(f'<span style="{";".join(style)}">{piece}</span>' if style else piece)
        lines.append("".join(parts))
    return "\n".join(lines)


def draw(shots: list[tuple[list[Row], Path]], size: int = 13) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        for rows, out in shots:
            page.set_content(PAGE.format(body=to_html(rows), size=size,
                                         fg=DEFAULT_FG, bg=DEFAULT_BG))
            page.wait_for_timeout(120)
            page.locator(".term").screenshot(path=str(out))
            print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", action="store_true", help="print the screens, draw nothing")
    args = ap.parse_args()

    home = Path(tempfile.mkdtemp(prefix="flyon-shot-"))
    try:
        subprocess.run([sys.executable, "-m", "flyon", "--home", str(home), "demo"],
                       check=True, capture_output=True,
                       env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))
        if args.text:
            for name, argv, what in SHOTS:
                print(f"\n──── {name}: {what}\n")
                print(as_text(capture(argv, home)))
            return 0

        (ROOT / "assets").mkdir(exist_ok=True)
        draw([(capture(argv, home), ROOT / "assets" / f"{name}.png")
              for name, argv, _ in SHOTS])
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
