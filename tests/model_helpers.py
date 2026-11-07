"""Shared builders for the model tests: a small pool where the numbers can be checked by hand."""

from __future__ import annotations

from lifo.model import Lifo, Params

LAUNCH = 100
EPOCH = 10
VINTAGE = 20


def small(**overrides: int) -> Params:
    values = {
        "virtual_near": 10_000,
        "virtual_tokens": 1_000_000,
        "max_supply": 800_000,
        "gate_bps": 1000,
        "sell_fee_bps": 100,
        "epoch_length": EPOCH,
        "vintage_length": VINTAGE,
        "launch_time": LAUNCH,
        "max_lots": 3,
    }
    values.update(overrides)
    return Params(**values)


def pool(**overrides: int) -> Lifo:
    return Lifo(small(**overrides))


def at(epoch: int, offset: int = 0) -> int:
    """A time inside the given epoch."""
    return LAUNCH + epoch * EPOCH + offset


def held(m: Lifo, account: str) -> list[tuple[int, int]]:
    return [(v, a) for v, a in m.lots.get(account, [])]


def locked_tokens(m: Lifo) -> int:
    """Tokens still held by requests: all of an unsettled one, the unfilled part of a settled unclaimed one."""
    total = 0
    for r in m.requests.values():
        if r.claimed:
            continue
        total += r.amount - (m.result(r.id).filled if r.epoch in m.settlements else 0)
    return total


def tail_counts(m: Lifo) -> dict[int, int]:
    """For every vintage v, how many existing tokens have vintage v or newer (balances and requests)."""
    by_vintage: dict[int, int] = {}
    for lots in m.lots.values():
        for v, a in lots:
            by_vintage[v] = by_vintage.get(v, 0) + a
    for r in m.requests.values():
        if r.claimed:
            continue
        if r.epoch in m.settlements:
            for v, a in m.result(r.id).returned:
                by_vintage[v] = by_vintage.get(v, 0) + a
        else:
            for v, a, _ in r.portions:
                by_vintage[v] = by_vintage.get(v, 0) + a
    tails: dict[int, int] = {}
    running = 0
    for v in sorted(by_vintage, reverse=True):
        running += by_vintage[v]
        tails[v] = running
    return tails


def tail_at(tails: dict[int, int], v: int) -> int:
    """Tokens of vintage v or newer, from tail_counts."""
    newer = [t for w, t in tails.items() if w >= v]
    return max(newer) if newer else 0
