"""The control markets of lifo/controls.py: each keeps LIFO's gate and fee and changes only what its name says."""

from __future__ import annotations

import pytest
from model_helpers import VINTAGE, at, small

from lifo.model import Lifo, Params, default_params
from lifo.sim import KINDS, CapMarket, OldestFirstMarket, ProRataMarket, simulate


def crowded(cls: type[Lifo]) -> tuple[Lifo, dict[str, int]]:
    """Old and new holders all ask to sell in one round, far more than the gate lets out."""
    m = cls(small(), transactional=False)
    m.buy("old", 3_000, at(0))
    m.buy("mid", 3_000, at(VINTAGE // 10))  # one vintage later
    m.buy("new", 3_000, at(2 * VINTAGE // 10))
    e = 5
    asked = {a: m.balance(a) for a in ("old", "mid", "new")}
    for a in asked:
        m.request_sell(a, asked[a], at(e))
    m.settle(at(e + 1))
    return m, asked


def filled(m: Lifo) -> dict[int, int]:
    (s,) = m.settlements.values()
    return s.filled


def test_every_order_lets_the_same_amount_leave_the_reserve() -> None:
    totals = {cls: crowded(cls)[0].settlements for cls in (Lifo, ProRataMarket, OldestFirstMarket)}
    fills = {cls: next(iter(s.values())) for cls, s in totals.items()}
    assert len({(f.total_filled, f.paid, f.bound) for f in fills.values()}) == 1
    assert next(iter(fills.values())).bound


def test_lifo_fills_the_newest_vintage_and_the_mirror_fills_the_oldest() -> None:
    lifo, oldest = filled(crowded(Lifo)[0]), filled(crowded(OldestFirstMarket)[0])
    assert lifo[2] > 0 and lifo[0] == 0
    assert oldest[0] > 0 and oldest[2] == 0


def test_pro_rata_fills_every_vintage_in_the_same_proportion_and_adds_up_exactly() -> None:
    m, _ = crowded(ProRataMarket)
    (s,) = m.settlements.values()
    assert sum(s.filled.values()) == s.total_filled
    for v in s.requested:
        assert s.filled[v] == pytest.approx(s.requested[v] * s.total_filled / s.total_requested, abs=1)


def test_the_mirror_market_sells_an_accounts_oldest_tokens_first() -> None:
    m = OldestFirstMarket(small(), transactional=False)
    m.buy("a", 1_000, at(0))
    m.buy("a", 1_000, at(VINTAGE // 10))
    first = m.lots["a"][0][1]
    rid = m.request_sell("a", first, at(5))
    assert [(v, a) for v, a, _ in m.requests[rid].portions] == [(0, first)]


def test_the_cap_market_sells_at_once_but_never_more_than_the_gate_in_one_round() -> None:
    p: Params = small()
    m = CapMarket(p, transactional=False)
    m.buy("a", 6_000, at(0))
    m.buy("b", 6_000, at(0))
    room = m.reserve * p.gate_bps // 10_000
    paid_a, _ = m.sell_now("a", m.balance("a"), at(3))
    paid_b, _ = m.sell_now("b", m.balance("b"), at(3))
    assert 0 < paid_a and paid_a + paid_b <= room and m.bound_now
    assert m.balance("a") > 0 and m.balance("b") > 0  # what the cap refused stays with its owners
    assert m.start_round(at(4)) and m.last_bound  # the next round opens with a fresh room
    assert m.room == m.reserve * p.gate_bps // 10_000


@pytest.mark.parametrize("kind", list(KINDS))
def test_every_rule_keeps_the_near_paid_in_in_the_reserve_or_paid_out(kind: str) -> None:
    run = simulate("insider_dump", kind, default_params(), 2)
    paid_in, paid_out = sum(run.invested.values()), sum(run.received.values())
    assert run.reserve[-1] * 10**24 == pytest.approx(paid_in - paid_out, rel=1e-6, abs=10**20)


def test_an_unknown_rule_is_refused() -> None:
    with pytest.raises(ValueError):
        simulate("calm", "random_order", default_params(), 1)
