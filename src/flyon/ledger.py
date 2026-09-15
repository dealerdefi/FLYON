"""
The record.

`flyon.jsonl` — append-only, one JSON object per line, each hashed over the one
before it:

    hash_n = sha256( seq | at | kind | body | hash_{n-1} )

Four kinds of line:

    scan        a block range was read, and what came out of it
    signal      a tracked wallet bought something, recorded the moment it was seen
    settled     that signal's window closed, and this is what happened
    note        anything a human wrote into the record by hand

## Why a tracker needs one

Because the interesting claim a signals page makes is not "this wallet bought" —
it is "we said so **before** it went up". Without an append-only record, that
claim rests entirely on the author's word, and a page that can quietly insert a
winning call after the fact is indistinguishable from one that does.

Every signal here is written at the block it was seen, hashed into a chain, and
settled later by a separate line that cannot replace the first one.

## What it is not

Proof of when anything happened. Whoever holds the file can rewrite it end to
end, and a test in this repository demonstrates exactly that. What it makes
obvious is a *single* edit — one flattering confidence, one deleted bad call —
which is the thing that actually happens.

Publish the head hash somewhere you do not control and the guarantee gets
sharper. The site prints it in the footer for exactly that reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64

KINDS = ("scan", "signal", "settled", "note")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def digest(seq: int, at: str, kind: str, body: dict[str, Any], prev: str) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return sha256("|".join([str(seq), at, kind, payload, prev]))


@dataclass(frozen=True)
class Entry:
    seq: int
    at: str
    kind: str
    body: dict[str, Any]
    prev: str
    hash: str

    def line(self) -> str:
        return json.dumps(
            {"seq": self.seq, "at": self.at, "kind": self.kind,
             "body": self.body, "prev": self.prev, "hash": self.hash},
            separators=(",", ":"), sort_keys=True,
        )


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.entries: list[Entry] = list(read(self.path))

    @property
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS

    def __len__(self) -> int:
        return len(self.entries)

    def of(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    def append(self, kind: str, at: str | None = None, **body: Any) -> Entry:
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind!r}. Known: {', '.join(KINDS)}")
        stamp = at or now()
        seq, prev = len(self.entries), self.head
        e = Entry(seq=seq, at=stamp, kind=kind, body=body, prev=prev,
                  hash=digest(seq, stamp, kind, body, prev))
        self.entries.append(e)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(e.line() + "\n")
        return e


def read(path: str | Path) -> Iterator[Entry]:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            yield Entry(seq=d["seq"], at=d["at"], kind=d["kind"],
                        body=d["body"], prev=d["prev"], hash=d["hash"])
        except (json.JSONDecodeError, KeyError):
            continue  # `verify` names it; reading does not pretend it is fine


@dataclass(frozen=True)
class Verdict:
    ok: bool
    lines: int
    head: str
    broke_at: int | None = None
    reason: str | None = None


def verify(path: str | Path) -> Verdict:
    p = Path(path)
    if not p.exists():
        return Verdict(True, 0, GENESIS, None, "no ledger yet")

    prev, expected, n = GENESIS, 0, 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        n += 1
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return Verdict(False, n, prev, expected, "line is not JSON")
        if d.get("seq") != expected:
            return Verdict(False, n, prev, d.get("seq"), f"sequence jumped: expected {expected}")
        if d.get("prev") != prev:
            return Verdict(False, n, prev, d["seq"], "prev hash does not match the line before it")
        if d.get("hash") != digest(d["seq"], d["at"], d["kind"], d["body"], d["prev"]):
            return Verdict(False, n, prev, d["seq"], "this line has been edited since it was written")
        prev, expected = d["hash"], expected + 1
    return Verdict(True, n, prev)
