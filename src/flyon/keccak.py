"""
Keccak-256, in about sixty lines.

Ethereum hashes with original Keccak, not the NIST SHA-3 that shipped later.
They differ by one byte — the padding — which is why `hashlib.sha3_256` gives
the wrong answer for an event topic and why every library in this corner of the
world carries its own copy of this file.

So does FLYON, rather than taking a dependency to compute six constants.

    keccak256(b"Transfer(address,address,uint256)").hex()
    ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef

Checked against the published vectors in the tests, including that one, which is
the most-quoted 32 bytes in the whole ecosystem.
"""

from __future__ import annotations

MASK = (1 << 64) - 1

ROUNDS = 24

#: Rotation offsets, indexed [x][y].
ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)


def rotl(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & MASK if shift else value


def permute(a: list[list[int]]) -> None:
    """Keccak-f[1600], in place on a 5×5 state of 64-bit lanes."""
    for rc in ROUND_CONSTANTS:
        # θ
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]

        # ρ and π
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = rotl(a[x][y], ROTATION[x][y])

        # χ
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y] & MASK) & b[(x + 2) % 5][y])

        # ι
        a[0][0] ^= rc


def keccak(data: bytes, rate_bytes: int, out_bytes: int, pad: int) -> bytes:
    state = [[0] * 5 for _ in range(5)]

    # pad10*1, with the domain byte that separates Keccak from SHA-3
    padded = bytearray(data)
    padded.append(pad)
    while len(padded) % rate_bytes:
        padded.append(0x00)
    padded[-1] |= 0x80

    for offset in range(0, len(padded), rate_bytes):
        block = padded[offset:offset + rate_bytes]
        for i in range(rate_bytes // 8):
            lane = int.from_bytes(block[i * 8:i * 8 + 8], "little")
            state[i % 5][i // 5] ^= lane
        permute(state)

    out = bytearray()
    while len(out) < out_bytes:
        for i in range(rate_bytes // 8):
            if len(out) >= out_bytes:
                break
            out += state[i % 5][i // 5].to_bytes(8, "little")
        if len(out) < out_bytes:
            permute(state)
    return bytes(out[:out_bytes])


def keccak256(data: bytes) -> bytes:
    """The one Ethereum means. Padding byte 0x01, not SHA-3's 0x06."""
    return keccak(data, rate_bytes=136, out_bytes=32, pad=0x01)


def eip55(address: str) -> str:
    """
    An address in the spelling where a wallet catches a typo for you.

    Every address FLYON prints for a human goes through this. Lowercase is what
    the chain returns and what the code compares; mixed case is what a person
    should be copying.
    """
    low = address.lower().removeprefix("0x")
    h = keccak256(low.encode()).hex()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(low))
