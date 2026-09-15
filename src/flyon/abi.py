"""
Decoding logs, by hand.

A log is a list of 32-byte topics and a blob of data. Turning that into "this
wallet swapped 1.2 of one token for 400 of another" is four lines of slicing and
one keccak hash, so that is what this file is, rather than a dependency.

Two event shapes cover almost everything that matters here:

    Transfer(address indexed from, address indexed to, uint256 value)
    Swap(address indexed sender, uint amount0In, uint amount1In,
         uint amount0Out, uint amount1Out, address indexed to)          v2
    Swap(address indexed sender, address indexed recipient,
         int256 amount0, int256 amount1, uint160 price, uint128 liq, int24 tick)  v3

The v3 amounts are **signed**: negative means the pool paid it out, positive
means the pool took it in. Reading that sign backwards flips every buy into a
sell, which is exactly the kind of bug that produces a confident, wrong
leaderboard — so it has a test of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from .keccak import keccak256

WORD = 64  # a 32-byte word, in hex characters


def topic(signature: str) -> str:
    """`Transfer(address,address,uint256)` → its 0x… topic0."""
    return "0x" + keccak256(signature.encode()).hex()


TRANSFER = topic("Transfer(address,address,uint256)")
SWAP_V2 = topic("Swap(address,uint256,uint256,uint256,uint256,address)")
SWAP_V3 = topic("Swap(address,address,int256,int256,uint160,uint128,int24)")

#: Pools announce their own tokens through these view calls.
SELECTOR = {
    "token0": "0x0dfe1681",
    "token1": "0xd21220a7",
    "decimals": "0x313ce567",
    "symbol": "0x95d89b41",
}


# ── primitives ───────────────────────────────────────────────────────────────


def words(data: str) -> list[str]:
    """The data blob, split into 32-byte words."""
    raw = data[2:] if data.startswith("0x") else data
    return [raw[i:i + WORD] for i in range(0, len(raw) - len(raw) % WORD, WORD)]


def to_uint(word: str) -> int:
    return int(word, 16) if word else 0


def to_int(word: str) -> int:
    """Two's complement. The sign is the difference between a buy and a sell."""
    v = to_uint(word)
    return v - (1 << 256) if v >= (1 << 255) else v


def to_address(word: str) -> str:
    """The last 20 bytes of a word, lowercased. Addresses are compared, not shown."""
    return "0x" + word[-40:].lower()


def strip_str(hexdata: str) -> str:
    """An ABI-encoded string, or a bytes32 one — tokens use both for `symbol()`."""
    ws = words(hexdata)
    if not ws:
        return ""

    # The dynamic layout announces itself: the first word is the offset, which
    # for a single return value is 32. Counting words instead gets an empty
    # string wrong — two words, no content — and hands back the offset decoded
    # as text, which is a space.
    if len(ws) >= 2 and to_uint(ws[0]) == 32:
        length = to_uint(ws[1])
        body = "".join(ws[2:])[: length * 2]
        try:
            return bytes.fromhex(body).decode("utf-8", "replace").strip("\x00")
        except ValueError:
            return ""

    try:  # the older tokens answer with a bare bytes32
        return bytes.fromhex(ws[0]).decode("utf-8", "replace").strip("\x00").strip()
    except ValueError:
        return ""


# ── events ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Transfer:
    token: str
    sender: str
    to: str
    value: int
    block: int
    tx: str
    index: int


@dataclass(frozen=True)
class Swap:
    """
    One swap, as the pool saw it.

    `amount0` and `amount1` are signed from **the pool's** point of view:
    positive is into the pool, negative is out of it. The trader's side is the
    mirror image, and `pnl.py` is the only place allowed to flip it.
    """

    pool: str
    sender: str
    to: str
    amount0: int
    amount1: int
    block: int
    tx: str
    index: int
    version: int


def log_meta(log: dict) -> tuple[int, str, int]:
    from .rpc import hexint

    return (
        hexint(log.get("blockNumber", "0x0")),
        str(log.get("transactionHash", "")),
        hexint(log.get("logIndex", "0x0")),
    )


def decode_transfer(log: dict) -> Transfer | None:
    topics = log.get("topics") or []
    if len(topics) < 3 or topics[0].lower() != TRANSFER:
        return None
    block, tx, index = log_meta(log)
    ws = words(log.get("data", "0x"))
    return Transfer(
        token=str(log.get("address", "")).lower(),
        sender=to_address(topics[1]),
        to=to_address(topics[2]),
        # ERC-721 puts the id in a fourth topic and leaves data empty; that is
        # not a transfer of value and is skipped rather than read as zero.
        value=to_uint(ws[0]) if ws else 0,
        block=block, tx=tx, index=index,
    )


def decode_swap(log: dict) -> Swap | None:
    topics = [t.lower() for t in (log.get("topics") or [])]
    if not topics:
        return None
    block, tx, index = log_meta(log)
    pool = str(log.get("address", "")).lower()
    ws = words(log.get("data", "0x"))

    if topics[0] == SWAP_V3 and len(topics) >= 3 and len(ws) >= 2:
        return Swap(pool=pool, sender=to_address(topics[1]), to=to_address(topics[2]),
                    amount0=to_int(ws[0]), amount1=to_int(ws[1]),
                    block=block, tx=tx, index=index, version=3)

    if topics[0] == SWAP_V2 and len(topics) >= 3 and len(ws) >= 4:
        in0, in1, out0, out1 = (to_uint(w) for w in ws[:4])
        # v2 reports four unsigned numbers; the signed pair says the same thing
        # in the shape the rest of the code expects.
        return Swap(pool=pool, sender=to_address(topics[1]), to=to_address(topics[2]),
                    amount0=in0 - out0, amount1=in1 - out1,
                    block=block, tx=tx, index=index, version=2)

    return None


def decode(logs: list[dict]) -> tuple[list[Swap], list[Transfer]]:
    swaps, transfers = [], []
    for log in logs:
        s = decode_swap(log)
        if s is not None:
            swaps.append(s)
            continue
        t = decode_transfer(log)
        if t is not None and t.value:
            transfers.append(t)
    return swaps, transfers
