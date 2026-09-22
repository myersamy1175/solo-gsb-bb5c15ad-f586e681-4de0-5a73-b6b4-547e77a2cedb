"""Timeline normalization engine.

Model
-----
For each cue *i* (in the given, fixed order) we keep its duration
``d_i = end_i - start_i`` unchanged and only move its start time to ``x_i``.

No-overlap with a minimum gap ``g`` between consecutive cues means::

    x_i + d_i + g <= x_{i + 1}

A locked cue has ``x_i = start_i``. Moving everything by the translation
``y_i = x_i - (start_0 + i*g + sum(d_0..d_{i-1}))`` turns the chain into the
simple isotonic constraint ``y_0 <= y_1 <= ... <= y_{n-1}`` and locked cues
become pinned values.

The objective is ``sum |x_i - start_i|`` which, expressed in ``y``, is a sum of
absolute deviations with different intercepts. Minimizing it under isotonic
and pinning constraints is *L1 isotonic regression*.

Pinned cues split the sequence into independent regions (before the first
pin, between two pins, after the last pin). Each region is an L1 isotonic
regression bounded by its surrounding pin values, solved exactly with a
median Pool Adjacent Violators Algorithm (PAVA) implemented on meldable
heaps merged small-to-large — O(n log² n), no native dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Cue:
    """One normalized cue."""

    id: str
    start_ms: int
    end_ms: int
    locked: bool


@dataclass(frozen=True)
class Conflict:
    """A pair of locked cues whose required span exceeds their locked span."""

    first_id: str
    second_id: str
    first_position_ms: int
    second_position_ms: int
    required_ms: int
    available_ms: int


class InfeasibleError(Exception):
    """Raised when the constraints cannot be satisfied."""

    def __init__(self, conflicts: Sequence[Conflict]):
        self.conflicts: List[Conflict] = list(conflicts)
        super().__init__("locked cues make the timeline infeasible")


@dataclass(frozen=True)
class _Block:
    low: List[float]  # max-heap (values negated)
    high: List[float]  # min-heap


def _median_isotonic(values: Sequence[float]) -> List[float]:
    """Unconstrained L1 isotonic regression.

    Returns the non-decreasing vector minimizing ``sum |out_i - values_i|``.
    Every block is represented by its lower median held in ``low``.
    """
    blocks: List[_Block] = []
    thetas: List[float] = []

    def merge(a: _Block, b: _Block) -> _Block:
        # Meld the heaps small-to-large so each element moves O(log n) times.
        if len(a.low) + len(a.high) < len(b.low) + len(b.high):
            a, b = b, a
        for v in b.low:
            _push(a, -v)
        for v in b.high:
            _push(a, v)
        return a

    def push_block(block: _Block) -> None:
        while thetas and thetas[-1] > _theta(block):
            old = blocks.pop()
            thetas.pop()
            block = merge(old, block)
        blocks.append(block)
        thetas.append(_theta(block))

    for value in values:
        push_block(_Block([-float(value)], []))

    out: List[float] = []
    for block in blocks:
        out.extend([_theta(block)] * (len(block.low) + len(block.high)))
    return out


def _theta(block: _Block) -> float:
    """Fitted value of a block: its lower median, kept in ``low``."""
    return -block.low[0]


def _push(block: _Block, value: float) -> None:
    """Insert ``value`` keeping the lower median at the top of ``low``."""
    low, high = block.low, block.high
    if low and value > -low[0]:
        heapq.heappush(high, value)
    else:
        heapq.heappush(low, -value)
    # With k elements the lower median is element number ceil(k / 2).
    need = (len(low) + len(high) + 1) // 2
    while len(low) < need:
        heapq.heappush(low, -heapq.heappop(high))
    while len(low) > need:
        heapq.heappush(high, -heapq.heappop(low))


def _bounded_isotonic(
    values: Sequence[float], lower: float, upper: Optional[float]
) -> List[float]:
    """L1 isotonic regression with ``lower <= out_i <= upper``.

    Clipping every observation to the feasible box before plain isotonic
    regression gives the bounded optimum: any coordinate pushed onto a bound
    is the L1 projection onto that box, and isotonic regression afterwards
    preserves the bounds.
    """
    clipped: List[float] = []
    for value in values:
        if value < lower:
            value = lower
        if upper is not None and value > upper:
            value = upper
        clipped.append(value)
    return _median_isotonic(clipped)


def solve(
    starts: Sequence[int],
    durations: Sequence[int],
    locked: Sequence[bool],
    ids: Sequence[str],
    gap_ms: int,
) -> Tuple[List[Cue], int]:
    """Compute the minimum-displacement overlap-free timeline.

    Returns ``(cues, total_displacement_ms)`` or raises :class:`InfeasibleError`.
    """
    n = len(starts)

    # Required cumulative left edge when the chain is packed at gap_ms:
    # base[i] = start_0 + i*gap + sum(d[0..i-1]); y_i = x_i - base[i].
    base: List[int] = [0] * n
    cumulative = 0
    for i in range(n):
        base[i] = starts[0] + i * gap_ms + cumulative
        cumulative += durations[i]

    pinned: List[Tuple[int, int]] = [
        (i, starts[i] - base[i]) for i in range(n) if locked[i]
    ]

    # Virtual pin (-1, y = -start_0) models the hard bound x_0 >= 0 ... and
    # also makes the prefix a regular region ending at the first real pin.
    virtual = (-1, -starts[0])

    conflicts: List[Conflict] = []
    prev_p, prev_v = virtual
    for p, v in pinned:
        if v < prev_v:
            # base[p] - base[prev_p] is the span forced by durations + gaps.
            if prev_p == -1:
                required = base[p]
                available = starts[p]
                first_id = "@start"
                first_pos = 0
            else:
                required = base[p] - base[prev_p]
                available = starts[p] - starts[prev_p]
                first_id = ids[prev_p]
                first_pos = starts[prev_p]
            conflicts.append(
                Conflict(
                    first_id=first_id,
                    second_id=ids[p],
                    first_position_ms=first_pos,
                    second_position_ms=starts[p],
                    required_ms=required,
                    available_ms=available,
                )
            )
        prev_p, prev_v = p, v
    if conflicts:
        raise InfeasibleError(conflicts)

    ys: List[Optional[float]] = [None] * n
    for p, v in pinned:
        ys[p] = float(v)

    # Walk regions: (-1, v0) .. pins .., with a virtual "infinity" end pin.
    boundaries = [virtual] + pinned
    for idx, (left_p, left_v) in enumerate(boundaries):
        if idx + 1 < len(boundaries):
            right_p, right_v = boundaries[idx + 1]
        else:
            right_p, right_v = (n, None)
        lo, hi = left_p + 1, right_p - 1
        if lo > hi:
            continue
        region_obs = [float(starts[i] - base[i]) for i in range(lo, hi + 1)]
        fitted = _bounded_isotonic(
            region_obs, float(left_v), None if right_v is None else float(right_v)
        )
        for offset, i in enumerate(range(lo, hi + 1)):
            ys[i] = fitted[offset]

    cues: List[Cue] = []
    total_shift = 0
    for i in range(n):
        assert ys[i] is not None
        new_start = base[i] + int(round(ys[i]))
        new_end = new_start + durations[i]
        total_shift += abs(new_start - starts[i])
        cues.append(
            Cue(
                id=ids[i],
                start_ms=new_start,
                end_ms=new_end,
                locked=locked[i],
            )
        )
    return cues, total_shift
