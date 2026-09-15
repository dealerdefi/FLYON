#!/usr/bin/env python3
"""
Photograph the page.

    python scripts/shot.py

Builds a demo, opens the page it produced in headless Chromium and saves
`assets/site.png`. The picture in the README is therefore the page the code
builds, not a mock-up of it — if something is misaligned there, it is
misaligned in the browser.

Development-only: needs Playwright. FLYON itself needs nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="flyon-shot-"))
    try:
        subprocess.run([sys.executable, "-m", "flyon", "--home", str(tmp), "demo"],
                       check=True, capture_output=True,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
        page_path = tmp / "site" / "index.html"
        out = ROOT / "assets" / "site.png"
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 1220}, device_scale_factor=2)
            pg.goto(page_path.as_uri())
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(out))
            b.close()
        print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
