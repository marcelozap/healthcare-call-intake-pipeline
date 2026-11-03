"""
mulberry32, ported from JavaScript so Python and the browser agree exactly.

Why this file exists
--------------------
The browser prototype generates its cohort in JavaScript. If Python generated a
*different* cohort, the app and the database would disagree and every number on
screen would be unverifiable. So this is a faithful port of the same generator,
called in the same order, from the same seed.

The result is that the database and the browser hold identical data by
construction rather than by coincidence — and `verify.py` proves it with a hash.

The fiddly part is that JavaScript's bitwise operators coerce their operands to
signed 32-bit integers, and `Math.imul` is a 32-bit multiply. Python integers are
arbitrary precision, so every step has to be masked back down or the two
sequences diverge on the very first call.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

_MASK = 0xFFFFFFFF


def to_int32(n: int) -> int:
    """Coerce to signed 32-bit — what `n|0` does in JavaScript."""
    n &= _MASK
    return n - 0x100000000 if n & 0x80000000 else n


def imul(a: int, b: int) -> int:
    """Math.imul: multiply as 32-bit signed integers."""
    return to_int32((a & _MASK) * (b & _MASK) & _MASK)


def ushr(n: int, bits: int) -> int:
    """JavaScript's `>>>` — shift right, filling with zeros."""
    return (n & _MASK) >> bits


class Random:
    """A seeded PRNG that produces the identical sequence to the browser's.

    These values are copied from the browser console, not from a Python run —
    that is what makes this a regression test rather than a tautology.

    >>> r = Random(880231)
    >>> [round(r(), 9) for _ in range(4)]
    [0.672641715, 0.566451875, 0.579618227, 0.3775094]
    """

    def __init__(self, seed: int) -> None:
        self.state = to_int32(seed)

    def __call__(self) -> float:
        self.state = to_int32(self.state + 0x6D2B79F5)
        s = self.state
        t = imul(s ^ ushr(s, 15), 1 | s)
        t = to_int32(to_int32(t + imul(t ^ ushr(t, 7), 61 | t)) ^ t)
        # final avalanche: (t ^ t>>>14) >>> 0
        return ((t ^ ushr(t, 14)) & _MASK) / 4294967296

    # ---- the same helpers the browser uses, in the same shapes ----------

    def pick(self, seq: Sequence[T]) -> T:
        return seq[int(self() * len(seq))]

    def weighted(self, seq: Sequence[T], weights: Sequence[float]) -> T:
        """Pick with relative weights. Consumes exactly one draw, like the browser."""
        r = self() * sum(weights)
        for item, w in zip(seq, weights):
            r -= w
            if r <= 0:
                return item
        return seq[-1]

    def between(self, lo: int, hi: int) -> int:
        """Inclusive integer in [lo, hi]."""
        return lo + int(self() * (hi - lo + 1))

    def gauss(self, mean: float, sd: float) -> float:
        """Box-Muller. Consumes exactly two draws, in the browser's order."""
        u = 1 - self()
        v = self()
        return mean + sd * math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)

    def chance(self, p: float) -> bool:
        return self() < p
