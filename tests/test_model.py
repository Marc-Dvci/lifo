"""One test per requirement in groups A, B and C of the spec, named after the rule it checks."""

from __future__ import annotations

import dataclasses

import pytest
from model_helpers import LAUNCH, at, held, locked_tokens, pool, small

from lifo import model
from lifo.model import (
    E_BALANCE,
    E_BUY_TOO_SMALL,
    E_MAX_SUPPLY,
    E_NOT_LAUNCHED,
    E_NOTHING,
    E_PARAMS,
    E_SELF,
    E_TIME,
    E_ZERO,
    Lifo,
    LifoError,
    ceil_div,
    gate_fill,
    sell_paid,
)

# --- A. The pool -----------------------------------------------------------------------------------------------------


def test_parameters_cannot_be_changed_after_initialisation() -> None:
    p = small()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.gate_bps = 1  # type: ignore[misc]


def test_the_model_has_no_owner_admin_or_upgrade_method() -> None:
    public = {n for n in dir(Lifo) if not n.startswith("_") and callable(getattr(Lifo, n))}
    assert public == {
        "balance",
        "buy",
        "buy_fee",
        "claim",
        "epoch",
        "is_young",
        "max_sellable_vintage",
        "payout",
        "quote_buy",
        "request_sell",
        "result",
        "sellable",
        "settle",
        "state",
        "transfer",
        "transfer_call",
        "vintage",
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"virtual_near": 0},
        {"max_supply": 1_000_000},
        {"max_supply": 0},
        {"gate_bps": 0},
        {"gate_bps": 10_001},
        {"sell_fee_bps": 10_000},
        {"sell_fee_bps": -1},
        {"epoch_length": 0},
        {"vintage_length": 15},
        {"launch_time": -1},
        {"max_lots": 0},
    ],
)
def test_initialisation_refuses_inconsistent_parameters(bad: dict[str, int]) -> None:
    with pytest.raises(LifoError, match=E_PARAMS):
        small(**bad)


def test_a_buy_gets_y_minus_ceil_k_over_x_plus_d_and_a_lot_of_the_current_vintage() -> None:
    m = pool()
    out = m.buy("a", 10_000, at(3))
    assert out == 1_000_000 - ceil_div(10_000 * 1_000_000, 20_000) == 500_000
    assert (m.reserve, m.supply) == (10_000, 500_000)
    assert held(m, "a") == [(1, 500_000)]  # epoch 3 is in vintage 1 (two epochs per vintage)


def test_a_buy_rounds_down_for_the_buyer() -> None:
    m = pool()
    out = m.buy("a", 3, at(0))
    exact = 1_000_000 - 10_000 * 1_000_000 / 10_003
    assert out == 299 and out < exact


def test_buys_execute_immediately_and_the_next_buy_pays_a_higher_price() -> None:
    m = pool()
    first = m.buy("a", 1_000, at(0))
    second = m.buy("b", 1_000, at(0))
    assert second < first


def test_a_buy_above_max_supply_is_refused_and_changes_nothing() -> None:
    m = pool()
    m.buy("a", 30_000, at(0))  # 750,000 tokens
    before = m.state()
    with pytest.raises(LifoError, match=E_MAX_SUPPLY):
        m.buy("b", 20_000, at(0))
    assert m.state() == before


def test_a_buy_exactly_at_max_supply_is_accepted() -> None:
    m = pool()
    assert m.buy("a", 40_000, at(0)) == 800_000


@pytest.mark.parametrize(("deposit", "error"), [(0, E_ZERO), (-5, E_ZERO), (1, E_BUY_TOO_SMALL)])
def test_a_buy_with_no_or_too_little_near_is_refused(deposit: int, error: str) -> None:
    m = pool(virtual_near=10**6, virtual_tokens=10**6, max_supply=10**5)
    with pytest.raises(LifoError, match=error):
        m.buy("a", deposit, at(0))


def test_a_sale_pays_gross_minus_a_fee_rounded_up_and_the_fee_stays_in_the_reserve() -> None:
    m = pool()
    m.buy("a", 10_000, at(0))
    x, y, k = m.x, m.y, m.k
    m.request_sell("a", 10_000, at(0))
    m.settle(at(1))
    gross = x - ceil_div(k, y + 10_000)
    fee = ceil_div(gross * 100, 10_000)
    assert m.settlements[0].paid == gross - fee == 392 - 4
    assert m.reserve == 10_000 - 388
    assert m.k > k


def test_selling_the_whole_supply_never_needs_more_than_the_reserve() -> None:
    m = pool()
    m.buy("a", 7_777, at(0))
    m.buy("b", 1_234, at(0))
    assert model.sell_gross(m.x, m.y, m.supply) <= m.reserve


# --- B. Vintages and transfers -----------------------------------------------------------------------------------------


def three_vintages() -> Lifo:
    m = pool()
    m.buy("a", 1_000, at(0))  # vintage 0
    m.buy("a", 1_000, at(2))  # vintage 1
    m.buy("a", 1_000, at(4))  # vintage 2
    return m


def test_tokens_leave_an_account_from_its_newest_lots_first() -> None:
    m = three_vintages()
    lots = held(m, "a")
    sent = m.transfer("a", "b", lots[2][1] + 10, at(4))
    assert sent == [(2, lots[2][1]), (1, 10)]
    assert held(m, "a") == [lots[0], (1, lots[1][1] - 10)]


def test_a_sell_request_locks_the_newest_tokens_first() -> None:
    m = three_vintages()
    lots = held(m, "a")
    rid = m.request_sell("a", lots[2][1] + 5, at(4))
    assert [(v, a) for v, a, _ in m.requests[rid].portions] == [(2, lots[2][1]), (1, 5)]


def test_a_receiver_keeps_the_vintages_and_merges_equal_ones() -> None:
    m = three_vintages()
    m.buy("b", 500, at(4))  # b holds vintage 2 too
    b2 = held(m, "b")[0][1]
    a2 = held(m, "a")[2][1]
    m.transfer("a", "b", a2 + 100, at(4))
    assert held(m, "b") == [(1, 100), (2, b2 + a2)]


def test_beyond_max_lots_the_two_oldest_lots_merge_into_the_older_vintage() -> None:
    m = three_vintages()
    lots = held(m, "a")
    m.buy("a", 1_000, at(6))  # a fourth vintage with MAX_LOTS = 3
    after = held(m, "a")
    assert len(after) == 3
    assert after[0] == (0, lots[0][1] + lots[1][1])
    assert after[1] == lots[2]


def test_a_merge_never_makes_tokens_newer() -> None:
    m = pool(max_lots=1)
    m.buy("a", 1_000, at(0))
    m.buy("a", 1_000, at(6))
    assert [v for v, _ in held(m, "a")] == [0]


def test_a_transfer_call_refund_comes_back_with_the_oldest_vintage_sent() -> None:
    m = three_vintages()
    lots = held(m, "a")
    amount = lots[2][1] + lots[1][1] + 1  # vintages 2, 1 and one unit of 0
    refund = m.transfer_call("a", "dex", amount, 50, at(4))
    assert refund == 50
    assert held(m, "a")[0] == (0, lots[0][1] - 1 + 50)
    assert m.balance("dex") == amount - 50


def test_a_transfer_call_refund_is_limited_to_what_the_receiver_still_holds() -> None:
    m = three_vintages()
    refund = m.transfer_call("a", "dex", 10, 99, at(4))
    assert refund == 10
    assert m.balance("dex") == 0


def test_a_transfer_call_refund_takes_the_receivers_oldest_tokens_and_never_returns_a_newer_vintage() -> None:
    m = three_vintages()
    m.buy("dex", 1_000, at(6))  # dex holds vintage 3
    m.transfer_call("a", "dex", 100, 100, at(6))
    assert held(m, "dex") == [(3, m.balance("dex"))]  # the refund came out of the oldest lot: vintage 3 stays whole
    assert held(m, "a")[-1][0] == 2


def test_a_refund_after_the_receiver_moved_the_tokens_turns_no_old_token_into_a_newer_vintage() -> None:
    """The review's sequence: the receiver passes on the vintage-1 tokens it got, then reports them all unused."""
    m = pool(epoch_length=10, vintage_length=10, launch_time=0)
    assert m.buy("receiver", 1_000, 0) == 90_909
    assert m.buy("sender", 1_000, 10) == 75_757
    refund = m.transfer_call("sender", "receiver", 75_757, 75_757, 10, moves=[("third", 75_757)])
    assert refund == 75_757
    assert held(m, "third") == [(1, 75_757)]
    assert held(m, "sender") == [(0, 75_757)], "the refund is vintage-0 tokens and stays vintage 0"
    assert held(m, "receiver") == [(0, 90_909 - 75_757)]
    newer = sum(a for lots in m.lots.values() for v, a in lots if v >= 1)
    assert newer == 75_757, "only the tokens bought at vintage 1 are vintage 1"


def test_old_tokens_sent_to_a_pool_that_refunds_come_back_old_and_leave_the_pools_newer_lots() -> None:
    m = pool(epoch_length=10, vintage_length=10, launch_time=0)
    m.buy("a", 1_000, 0)  # vintage 0
    m.buy("exchange", 5_000, 50)  # vintage 5, the pool's own newer stock
    stock = m.balance("exchange")
    assert m.transfer_call("a", "exchange", 1_000, 1_000, 50) == 1_000
    assert held(m, "a") == [(0, m.balance("a"))]
    assert held(m, "exchange") == [(5, stock)]
    # the pool passes 1,000 on (its newest first, B1), then still refunds 1,000: they come out of its oldest lot
    assert m.transfer_call("a", "exchange", 1_000, 1_000, 50, moves=[("b", 1_000)]) == 1_000
    assert held(m, "a") == [(0, m.balance("a"))], "never the pool's vintage 5"
    assert held(m, "b") == [(5, 1_000)]
    assert held(m, "exchange") == [(5, stock - 1_000)]


def test_a_refund_out_of_lots_newer_than_the_oldest_sent_is_given_the_oldest_vintage_sent() -> None:
    """The receiver passed every token on and bought new ones (from `ft_on_transfer`) before the refund."""
    m = pool(epoch_length=10, vintage_length=10, launch_time=0)
    m.buy("a", 1_000, 0)
    m.buy("receiver", 1_000, 50)  # vintage 5, bought after the transfer left the receiver empty
    assert m._refund("a", "receiver", 500, 0, 500) == (500, [[0, 500]])
    assert held(m, "a") == [(0, m.balance("a"))]


@pytest.mark.parametrize(
    ("args", "error"),
    [(("a", "a", 5), E_SELF), (("a", "b", 0), E_ZERO), (("a", "b", 10**9), E_BALANCE)],
)
def test_a_transfer_to_oneself_of_nothing_or_of_too_much_is_refused(args: tuple[str, str, int], error: str) -> None:
    m = three_vintages()
    before = m.state()
    with pytest.raises(LifoError, match=error):
        m.transfer(*args, at(4))
    assert m.state() == before


# --- C. Sell requests, the gate and settlement ---------------------------------------------------------------------


def test_a_sell_request_locks_tokens_into_the_current_epoch() -> None:
    m = three_vintages()
    total = m.balance("a")
    rid = m.request_sell("a", 100, at(5, 3))
    assert m.requests[rid].epoch == 5
    assert m.balance("a") == total - 100
    assert locked_tokens(m) == 100


@pytest.mark.parametrize(("amount", "error"), [(0, E_ZERO), (10**9, E_BALANCE)])
def test_a_sell_request_of_nothing_or_of_more_than_the_balance_is_refused(amount: int, error: str) -> None:
    m = three_vintages()
    with pytest.raises(LifoError, match=error):
        m.request_sell("a", amount, at(4))


def test_an_epoch_is_not_settled_before_it_ends() -> None:
    m = three_vintages()
    m.request_sell("a", 100, at(5))
    assert m.settle(at(5, 9)) == 0
    assert m.settle(at(6)) == 1
    assert 5 in m.settlements


def test_any_call_settles_the_finished_epoch_first() -> None:
    m = three_vintages()
    m.request_sell("a", 100, at(5))
    m.buy("z", 10, at(6))
    assert 5 in m.settlements


def test_settlement_uses_the_pool_after_every_buy_of_the_epoch() -> None:
    m = three_vintages()
    n = three_vintages()
    m.request_sell("a", 1_000, at(5))
    n.request_sell("a", 1_000, at(5))
    n.buy("z", 5_000, at(5, 9))  # a buy later in the same epoch raises the price the batch gets
    m.settle(at(6))
    n.settle(at(6))
    assert n.settlements[5].paid > m.settlements[5].paid


def gate_pool() -> Lifo:
    """Reserve 10,000, so cap = 1,000 at 10 %; four sellers, vintages 0 to 3."""
    m = pool(max_lots=4)
    for i, who in enumerate("abcd"):
        m.buy(who, 2_500, at(2 * i))
    return m


def test_the_gate_fill_is_the_largest_amount_whose_payout_fits_the_cap() -> None:
    m = gate_pool()
    cap = m.reserve * 1000 // 10_000
    f = gate_fill(m.x, m.y, cap, 100)
    assert f is not None
    assert sell_paid(m.x, m.y, f, 100) <= cap < sell_paid(m.x, m.y, f + 1, 100)


def test_the_gate_cannot_bind_when_the_cap_covers_the_whole_curve() -> None:
    assert gate_fill(10, 10, 10**6, 100) is None


def test_when_requests_fit_under_the_gate_every_request_fills_in_full() -> None:
    m = gate_pool()
    for who in "abcd":
        m.request_sell(who, 100, at(7))
    m.settle(at(8))
    s = m.settlements[7]
    assert not s.bound and s.filled == s.requested and s.total_filled == 400


def test_when_the_gate_binds_newer_vintages_fill_first_and_older_get_nothing() -> None:
    m = gate_pool()
    for who in "abcd":
        m.request_sell(who, m.balance(who), at(7))
    m.settle(at(8))
    s = m.settlements[7]
    assert s.bound and s.max_fill is not None and s.total_filled == s.max_fill
    newest_first = [s.filled[v] for v in (3, 2, 1, 0)]
    full = [s.requested[v] for v in (3, 2, 1, 0)]
    boundary = next(i for i, (f, r) in enumerate(zip(newest_first, full, strict=True)) if f < r)
    assert newest_first[:boundary] == full[:boundary]
    assert all(f == 0 for f in newest_first[boundary + 1 :])


def test_the_boundary_vintage_is_shared_pro_rata_and_the_shares_add_up_exactly() -> None:
    m = pool()
    m.buy("a", 1_000, at(0))
    m.transfer("a", "b", 30_001, at(0))
    m.transfer("a", "c", 30_002, at(0))
    ids = [m.request_sell(who, m.balance(who), at(1)) for who in "abc"]
    m.settle(at(2))
    s = m.settlements[1]
    assert s.bound and s.filled[0] < s.requested[0]
    results = [m.result(i) for i in ids]
    assert sum(r.filled for r in results) == s.filled[0]
    for r, i in zip(results, ids, strict=True):
        exact = m.requests[i].amount * s.filled[0] / s.requested[0]
        assert abs(r.filled - exact) < 1


def test_every_filled_token_gets_the_same_price_and_the_dust_returns_to_the_reserve() -> None:
    m = gate_pool()
    for who in "abcd":
        m.request_sell(who, m.balance(who), at(7))
    m.settle(at(8))
    s = m.settlements[7]
    reserve_before = m.reserve
    paid = 0
    for who in "abcd":
        c = m.claim(who, at(8))[0]
        assert c.paid == c.filled * s.paid // s.total_filled
        paid += c.paid
    assert s.dust_returned == s.paid - paid
    assert m.reserve == reserve_before + s.dust_returned
    assert m.unclaimed_near == 0


def test_unfilled_tokens_go_back_with_their_own_vintage() -> None:
    m = gate_pool()
    m.request_sell("a", m.balance("a"), at(7))
    m.request_sell("d", m.balance("d"), at(7))
    m.settle(at(8))
    m.claim("a", at(8))
    assert [v for v, _ in held(m, "a")] == [0]


def test_an_unfilled_seller_is_ordered_by_vintage_again_not_by_when_it_first_asked() -> None:
    m = gate_pool()
    m.request_sell("a", m.balance("a"), at(7))  # vintage 0 asks first
    m.settle(at(8))
    m.claim("a", at(8))
    m.request_sell("a", m.balance("a"), at(9))
    m.request_sell("d", m.balance("d"), at(9))  # vintage 3 asks later
    m.settle(at(10))
    s = m.settlements[9]
    assert s.bound and s.filled[3] > 0
    assert s.filled[0] == 0


def test_a_settlement_records_vintages_not_sellers() -> None:
    one = gate_pool()
    many = gate_pool()
    one.request_sell("a", 3_000, at(7))
    for _ in range(3):
        many.request_sell("a", 1_000, at(7))
    one.settle(at(8))
    many.settle(at(8))
    a, b = one.settlements[7], many.settlements[7]
    assert (a.requested, a.filled, a.paid) == (b.requested, b.filled, b.paid)


def test_a_request_is_paid_exactly_once() -> None:
    m = gate_pool()
    m.request_sell("a", 100, at(7))
    m.settle(at(8))
    first = m.claim("a", at(8))
    assert len(first) == 1 and m.paid_out["a"] == first[0].paid
    with pytest.raises(LifoError, match=E_NOTHING):
        m.claim("a", at(8))
    assert m.paid_out["a"] == first[0].paid


def test_the_next_call_of_the_owner_claims_for_it() -> None:
    m = gate_pool()
    m.request_sell("a", 100, at(7))
    m.transfer("a", "b", 1, at(8))
    assert m.requests[0].claimed
    assert "a" in m.paid_out


def test_a_request_with_nothing_settled_cannot_be_claimed() -> None:
    m = gate_pool()
    m.request_sell("a", 100, at(7))
    with pytest.raises(LifoError, match=E_NOTHING):
        m.claim("a", at(7))


# --- Time and atomicity ----------------------------------------------------------------------------------------------


def test_nothing_happens_before_launch() -> None:
    with pytest.raises(LifoError, match=E_NOT_LAUNCHED):
        pool().buy("a", 100, LAUNCH - 1)


def test_time_cannot_go_backwards() -> None:
    m = pool()
    m.buy("a", 100, at(3))
    with pytest.raises(LifoError, match=E_TIME):
        m.buy("a", 100, at(2))


def test_a_refused_call_does_not_even_run_the_settlement_it_triggered() -> None:
    m = gate_pool()
    m.request_sell("a", 100, at(7))
    before = m.state()
    with pytest.raises(LifoError, match=E_ZERO):
        m.buy("a", 0, at(8))
    assert m.state() == before and 7 not in m.settlements
