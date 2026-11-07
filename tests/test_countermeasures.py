"""The rules added after the refresh attack (requirements-v2): the 24 h vintage and the options that were tested and not
adopted (buy fee, young-token sell fee, minimum age). Each option is a fixed parameter, off when it is 0."""

from __future__ import annotations

import pytest
from model_helpers import at, pool

from lifo.model import (
    DAY,
    E_BALANCE,
    E_YOUNG,
    Lifo,
    LifoError,
    buy_out,
    default_params,
)


def test_a_refresh_in_a_coarse_vintage_is_filled_pro_rata_with_everyone_who_bought_that_day() -> None:
    """Vintages of 10 epochs: the insider's fresh tokens and an honest buyer's, bought on the same 'day', share the fill."""

    def fills(vintage_length: int) -> tuple[int, int, int, int, int]:
        m = pool(vintage_length=vintage_length, max_supply=900_000)
        m.buy("old", 5_000, at(0))
        m.buy("honest", 3_000, at(12))
        m.buy("insider", 3_000, at(15))  # the refresh: the insider's tokens are as new as it can make them
        sizes = [m.balance(who) for who in ("honest", "insider")]
        for who in ("old", "honest", "insider"):
            m.request_sell(who, m.balance(who), at(18))
        m.settle(at(19))
        old, honest, insider = (sum(c.filled for c in m.claim(who, at(19))) for who in ("old", "honest", "insider"))
        return old, honest, insider, sizes[0], sizes[1]

    old, honest, insider, honest_size, insider_size = fills(
        100
    )  # the 'day' is 10 epochs: epochs 12 and 15 are the same vintage
    assert old == 0 and honest > 0 and insider > 0
    assert (
        abs(honest * insider_size - insider * honest_size) <= honest_size + insider_size
    )  # the same share, within a unit
    old, honest, insider, _, _ = fills(10)  # one-epoch vintages: the newest tokens take the whole fill
    assert old == 0 and honest == 0 and insider > 0


def test_a_buy_fee_stays_in_the_reserve_and_buys_nothing() -> None:
    m = pool(buy_fee_bps=50)
    x, y, k0 = m.x, m.y, m.k
    out = m.buy("a", 2_000, at(0))
    fee = -(-2_000 * 50 // 10_000)
    assert out == buy_out(x, y, 2_000 - fee) and m.reserve == 2_000 and m.quote_buy(2_000) < buy_out(x, y, 2_000)
    assert m.k > k0


def test_young_tokens_pay_the_young_fee_and_the_old_ones_do_not() -> None:
    def paid_in(epoch: int, **extra: int) -> tuple[int, int, int]:
        m = pool(vintage_length=20, **extra)
        m.buy("a", 3_000, at(0))  # vintage 0 ends 20 after launch: young until 60 after launch, that is epoch 6
        reserve_before, k_before = m.reserve, m.k
        m.request_sell("a", m.balance("a"), at(epoch))
        m.settle(at(epoch + 1))
        s = m.settlements[epoch]
        assert m.reserve == reserve_before - s.paid and m.k >= k_before  # the fee never leaves the reserve
        return s.paid, s.gross, sum(c.paid for c in m.claim("a", at(epoch + 1)))

    paid, gross, claimed = paid_in(3, young_age=40, young_fee_bps=500)
    assert paid == claimed == gross * 9_500 // 10_000 < gross
    paid, gross, claimed = paid_in(6, young_age=40, young_fee_bps=500)
    assert paid == claimed == gross
    paid, gross, claimed = paid_in(3)
    assert paid == gross


def test_every_token_of_a_vintage_is_young_or_old_together() -> None:
    m = pool(vintage_length=20, young_age=40, young_fee_bps=500)
    assert [m.is_young(e, 0) for e in range(8)] == [True] * 6 + [False] * 2
    assert not pool().is_young(0, 0)  # no young fee: nothing is young


def test_tokens_younger_than_the_minimum_age_cannot_be_requested_but_can_be_transferred() -> None:
    m = pool(vintage_length=20, min_age=40)
    m.buy("a", 3_000, at(0))  # vintage 0, can be requested from epoch 6
    with pytest.raises(LifoError, match=E_YOUNG):
        m.request_sell("a", 1, at(5, 9))
    m.transfer("a", "b", 10, at(5, 9))
    m.request_sell("a", 1, at(6))
    m.buy("a", 3_000, at(6))  # vintage 3: too young at epoch 7
    older = sum(a for v, a in m.lots["a"] if v == 0)
    with pytest.raises(LifoError, match=E_YOUNG):
        m.request_sell("a", older + 1, at(7))
    assert m.request_sell("a", older - 1, at(7)) >= 0  # only the old lot is touched
    with pytest.raises(LifoError, match=E_BALANCE):
        m.request_sell("a", m.balance("a") + 1, at(7))


def test_the_launch_parameters_use_a_24_hour_vintage_and_none_of_the_untested_options() -> None:
    p = default_params()
    assert p.vintage_length == DAY
    assert (p.buy_fee_bps, p.young_age, p.young_fee_bps, p.min_age) == (0, 0, 0, 0)
    Lifo(p)  # valid


@pytest.mark.parametrize(
    "bad",
    [
        {"buy_fee_bps": 10_000},
        {"young_fee_bps": 100},
        {"young_age": -1},
        {"min_age": -1},
        {"young_age": 5, "young_fee_bps": -1},
    ],
)
def test_the_new_parameters_are_checked(bad: dict[str, int]) -> None:
    with pytest.raises(LifoError):
        pool(**bad)
