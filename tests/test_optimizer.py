"""Correctness tests for the optimization engine.

The PAVA solution is cross-checked on small random instances against
independent exhaustive enumeration over non-decreasing level sequences.
An L1 isotonic optimum always uses block levels drawn from the observed
values (block optima are medians), so enumeration is exact.
"""

from __future__ import annotations

import itertools
import random

import pytest

from app.optimizer import InfeasibleError, _median_isotonic, solve


def brute_force_isotonic(values):
    """Exact L1 isotonic regression by enumeration."""
    n = len(values)
    candidates = sorted(set(values))
    best_cost = None
    best = None
    for levels in itertools.combinations_with_replacement(candidates, n):
        cost = sum(abs(levels[i] - values[i]) for i in range(n))
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = list(levels)
    return [float(v) for v in best]


@pytest.mark.parametrize("seed", range(300))
def test_median_isotonic_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 7)
    values = [rng.randint(-5, 5) for _ in range(n)]
    got = _median_isotonic(values)
    ref = brute_force_isotonic(values)

    assert all(got[i] <= got[i + 1] for i in range(n - 1))
    got_cost = sum(abs(a - b) for a, b in zip(got, values))
    ref_cost = sum(abs(a - b) for a, b in zip(ref, values))
    assert got_cost == pytest.approx(ref_cost)


def assert_feasible(cues, starts, durations, locked, gap):
    n = len(cues)
    for i, cue in enumerate(cues):
        assert cue.start_ms >= 0
        assert cue.end_ms - cue.start_ms == durations[i]
        if locked[i]:
            assert cue.start_ms == starts[i]
    for i in range(n - 1):
        assert cues[i + 1].start_ms - cues[i].end_ms >= gap


def brute_force_solve(starts, durations, locked, gap):
    """Exact optimum via region-wise enumeration; None iff pins conflict."""
    n = len(starts)
    base = [0] * n
    cumulative = 0
    for i in range(n):
        base[i] = starts[0] + i * gap + cumulative
        cumulative += durations[i]

    pins = [(i, starts[i] - base[i]) for i in range(n) if locked[i]]
    boundaries = [(-1, -starts[0])] + pins
    total = 0
    for idx, (lp, lv) in enumerate(boundaries):
        rp, rv = boundaries[idx + 1] if idx + 1 < len(boundaries) else (n, None)
        if rv is not None and rv < lv:
            return None
        lo, hi = lp + 1, rp - 1
        if lo > hi:
            continue
        obs = [starts[i] - base[i] for i in range(lo, hi + 1)]
        candidates = sorted(set(obs) | {lv} | ({rv} if rv is not None else set()))
        candidates = [c for c in candidates if c >= lv and (rv is None or c <= rv)]
        r = hi - lo + 1
        best = None
        for levels in itertools.combinations_with_replacement(candidates, r):
            cost = sum(abs(levels[k] - obs[k]) for k in range(r))
            if best is None or cost < best:
                best = cost
        total += best
    return total


@pytest.mark.parametrize("seed", range(200))
def test_solve_matches_bruteforce(seed):
    rng = random.Random(1000 + seed)
    n = rng.randint(1, 5)
    starts = [rng.randint(0, 8)]
    for _ in range(n - 1):
        starts.append(starts[-1] + rng.randint(-2, 5))
    durations = [rng.randint(1, 4) for _ in range(n)]
    gap = rng.randint(0, 2)
    locked = [rng.random() < 0.35 for _ in range(n)]
    ids = [str(i) for i in range(n)]

    try:
        cues, cost = solve(starts, durations, locked, ids, gap)
    except InfeasibleError:
        assert brute_force_solve(starts, durations, locked, gap) is None
        return

    assert_feasible(cues, starts, durations, locked, gap)
    ref = brute_force_solve(starts, durations, locked, gap)
    assert ref is not None
    assert cost == ref


def test_locked_cues_conflict():
    # cue0 locked at 0 (d=100), cue1 locked at 50 (d=10), gap 0 -> overlap.
    starts = [0, 50]
    durations = [100, 10]
    locked = [True, True]
    with pytest.raises(InfeasibleError) as exc:
        solve(starts, durations, locked, ["a", "b"], 0)
    conflict = exc.value.conflicts[0]
    assert conflict.first_id == "a"
    assert conflict.second_id == "b"
    assert conflict.required_ms == 100
    assert conflict.available_ms == 50
    assert conflict.required_ms - conflict.available_ms == 50


def test_unlocked_cues_are_always_repairable():
    starts = [1000, 0]  # heavily inverted
    durations = [500, 200]
    cues, cost = solve(starts, durations, [False, False], ["a", "b"], 0)
    assert_feasible(cues, starts, durations, [False, False], 0)
    assert cues[0].start_ms == 0
    assert cues[1].start_ms == 500
    assert cost == 1500
