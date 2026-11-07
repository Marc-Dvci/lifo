"""The simulator: reproducible, fair between FIFO and LIFO, and it conserves NEAR like the model does."""

from __future__ import annotations

import numpy as np
import pytest

from lifo.model import DAY, default_params
from lifo.sim import draw_agents, measures, simulate

P = default_params()


def test_the_same_seed_gives_the_same_run() -> None:
    a, b = simulate("panic", "lifo", P, 3), simulate("panic", "lifo", P, 3)
    assert a.price == b.price and a.received == b.received


def test_fifo_and_lifo_face_the_same_agents() -> None:
    a = draw_agents("insider_dump", 5, DAY // P.epoch_length)
    b = draw_agents("insider_dump", 5, DAY // P.epoch_length)
    assert a.group == b.group and np.array_equal(a.size, b.size) and np.array_equal(a.theta, b.theta)


@pytest.mark.parametrize("kind", ["fifo", "lifo"])
def test_every_near_paid_in_is_in_the_reserve_or_paid_out(kind: str) -> None:
    run = simulate("insider_dump", kind, P, 2)
    paid_in = sum(run.invested.values())
    paid_out = sum(run.received.values())
    assert run.reserve[-1] * 10**24 == pytest.approx(paid_in - paid_out, rel=1e-6, abs=10**20)


def test_fifo_sellers_never_wait_and_the_gate_never_binds() -> None:
    m = measures(simulate("insider_dump", "fifo", P, 1), draw_agents("insider_dump", 1, DAY // P.epoch_length).group)
    assert m["oldest_wait_max_h"] == 0 and m["gate_bound_epochs"] == 0


def test_in_the_insider_dump_lifo_pays_the_newest_third_more_than_fifo() -> None:
    groups = draw_agents("insider_dump", 1, DAY // P.epoch_length).group
    fifo = measures(simulate("insider_dump", "fifo", P, 1), groups)
    lifo = measures(simulate("insider_dump", "lifo", P, 1), groups)
    assert lifo["crash_share_newest_third"] > fifo["crash_share_newest_third"]
    assert lifo["late_result"] > fifo["late_result"]
