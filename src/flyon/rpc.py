"""
Reading a chain.

Everything FLYON knows comes through this file, and this file only ever asks
questions. There are six JSON-RPC methods here and all six read. There is no
`eth_sendRawTransaction`, no signing, no key handling, no wallet — **FLYON cannot
move anyone's money, because the ability is absent from the source**, and a test
parses this file to prove the words are not in it.

That matters more here than in most projects. A page that watches wallets is a
page people arrive at with their own wallet connected, and the honest version of
it should be unable to touch anything even if it wanted to.

## Two transports

`Http` talks to a real endpoint. `Replay` answers from a file of recorded
responses and never opens a socket, which is how the test suite runs offline and
how a bug in the indexer can be reproduced exactly a week later:

    flyon index --record run.json     # keep every response
    flyon index --replay  run.json    # the same blocks, forever

A recorded run is also the only honest way to show someone a number and let them
re-derive it without trusting your server.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: The complete list. Adding a seventh is a decision, not a convenience.
READ_METHODS = (
    "eth_chainId",
    "eth_blockNumber",
    "eth_getLogs",
    "eth_getBlockByNumber",
    "eth_call",
    "eth_getTransactionReceipt",
)

TIMEOUT = 25


class RpcError(Exception):
    """The chain could not be read, and the message says which call failed."""


def key_of(method: str, params: list[Any]) -> str:
    """The cache key for a call. Stable across runs, so a replay file diffs well."""
    return method + ":" + json.dumps(params, sort_keys=True, separators=(",", ":"))


# ── transports ───────────────────────────────────────────────────────────────


@dataclass
class Http:
    """A real endpoint. Retries the transient things and gives up loudly."""

    url: str
    tries: int = 3
    pause: float = 0.8
    #: Every call made, in order. `--record` writes this out.
    tape: dict[str, Any] = field(default_factory=dict)
    calls: int = 0

    def __call__(self, method: str, params: list[Any]) -> Any:
        if method not in READ_METHODS:
            raise RpcError(f"{method} is not a read method. FLYON only reads")

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        last = ""
        for attempt in range(self.tries):
            try:
                req = urllib.request.Request(
                    self.url, data=body.encode(),
                    headers={"content-type": "application/json",
                             "user-agent": "flyon/0.1 (+https://github.com/dealerdefi/FLYON)"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    payload = json.loads(r.read().decode())
                break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
                last = str(e)
                if attempt + 1 < self.tries:
                    time.sleep(self.pause * (attempt + 1))
        else:
            raise RpcError(f"{self.url} unreachable after {self.tries} tries: {last}")

        if "error" in payload:
            raise RpcError(f"{method}: {payload['error'].get('message', 'rpc error')}")
        if "result" not in payload:
            raise RpcError(f"{method}: no result in the answer")

        self.calls += 1
        self.tape[key_of(method, params)] = payload["result"]
        return payload["result"]


@dataclass
class Replay:
    """
    Answers from a recorded file. Opens no socket, ever.

    A call that was never recorded raises rather than returning something
    plausible — a replay that quietly invents an answer is worse than no replay,
    because the number it produces looks exactly like a real one.
    """

    tape: dict[str, Any]
    calls: int = 0
    strict: bool = True

    @classmethod
    def load(cls, path: str | Path) -> "Replay":
        return cls(tape=json.loads(Path(path).read_text(encoding="utf-8")))

    def __call__(self, method: str, params: list[Any]) -> Any:
        if method not in READ_METHODS:
            raise RpcError(f"{method} is not a read method. FLYON only reads")
        k = key_of(method, params)
        if k not in self.tape:
            if self.strict:
                raise RpcError(f"nothing recorded for {k}")
            return None
        self.calls += 1
        return self.tape[k]


# ── the client ───────────────────────────────────────────────────────────────


def hexint(v: Any) -> int:
    """`0x1a` or `26` → 26. Chains are inconsistent about which they send."""
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


@dataclass
class Chain:
    """The six questions, with the chain-id guard in front of all of them."""

    call: Callable[[str, list[Any]], Any]
    #: What the caller believes it is talking to. Checked once, before anything else.
    expect_chain_id: int | None = None
    _checked: bool = False

    def _guard(self) -> None:
        if self._checked or self.expect_chain_id is None:
            return
        live = hexint(self.call("eth_chainId", []))
        if live != self.expect_chain_id:
            raise RpcError(
                f"the endpoint reports chain {live}, but you asked for "
                f"{self.expect_chain_id}. Refusing to read the wrong chain"
            )
        self._checked = True

    # ── reads ────────────────────────────────────────────────────────────────

    def chain_id(self) -> int:
        return hexint(self.call("eth_chainId", []))

    def head(self) -> int:
        self._guard()
        return hexint(self.call("eth_blockNumber", []))

    def logs(self, from_block: int, to_block: int,
             address: list[str] | None = None,
             topics: list[Any] | None = None) -> list[dict]:
        self._guard()
        q: dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address:
            q["address"] = [a.lower() for a in address]
        if topics:
            q["topics"] = topics
        out = self.call("eth_getLogs", [q])
        return list(out or [])

    def block(self, number: int, full: bool = False) -> dict:
        self._guard()
        return self.call("eth_getBlockByNumber", [hex(number), full]) or {}

    def timestamp(self, number: int) -> int:
        return hexint(self.block(number).get("timestamp", "0x0"))

    def call_contract(self, to: str, data: str, at: str = "latest") -> str:
        """A view call. `eth_call` cannot change state — that is the whole point."""
        self._guard()
        return self.call("eth_call", [{"to": to.lower(), "data": data}, at]) or "0x"


def open_chain(url: str, expect: int | None = None, replay: str | Path | None = None) -> Chain:
    """A chain to read from, live or recorded."""
    transport: Callable[[str, list[Any]], Any]
    transport = Replay.load(replay) if replay else Http(url=url)
    return Chain(call=transport, expect_chain_id=expect)
