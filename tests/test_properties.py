"""Hypothesis properties of the model (M2): random sequences of buys, transfers, requests, settlements and claims.

Every invariant of the state machine is checked after every step of every example (G2: at least 2,000 examples each;
the state machine runs 6,000 sequences because only part of them reach a settlement; --hypothesis-show-statistics
prints how many).
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from model_helpers import locked_tokens, tail_at, tail_counts

from lifo.model import Lifo, LifoError, Params, gate_fill, sell_gross, sell_paid

EXAMPLES = 2000
pytestmark = pytest.mark.timeout(900)  # 2,000 examples each take longer than the default 30 s
ACCOUNTS = ("a", "b", "c", "d")
who = st.sampled_from(ACCOUNTS)
fraction = st.integers(min_value=1, max_value=1000)  # per mille of the balance
step = st.integers(min_value=0, max_value=15)  # time advance, the epoch is 10


@st.composite
def params(draw: st.DrawFn) -> Params:
    vt = draw(st.integers(min_value=10**3, max_value=10**12))
    epoch = 10
    return Params(
        virtual_near=draw(st.integers(min_value=10**3, max_value=10**12)),
        virtual_tokens=vt,
        max_supply=draw(st.integers(min_value=vt // 2, max_value=vt - 1)),
        gate_bps=draw(st.sampled_from([1, 50, 200, 500, 1000, 5000, 10_000])),
        sell_fee_bps=draw(st.sampled_from([0, 1, 50, 100, 300, 9999])),
        epoch_length=epoch,
        vintage_length=epoch * draw(st.integers(min_value=1, max_value=3)),
        launch_time=0,
        max_lots=draw(st.integers(min_value=1, max_value=4)),
    )


class Market(RuleBasedStateMachine):
    """A pool driven at random. The machine tracks deposits and claims itself, then checks the model against them."""

    m: Lifo

    @initialize(p=params())
    def launch(self, p: Params) -> None:
        self.m = Lifo(p)
        self.t = 0
        self.deposited = 0
        self.claimed_ids: set[int] = set()
        self.checked: set[int] = set()
        self.tails = tail_counts(self.m)
        self.bought = 0
        for i, account in enumerate(ACCOUNTS):  # every account starts with tokens of its own vintage
            self.t = i * p.vintage_length
            self.m.buy(account, p.virtual_near // 20, self.t)
            self.deposited += p.virtual_near // 20
        self.tails = tail_counts(self.m)

    def advance(self, dt: int) -> int:
        self.t += dt
        return self.t

    def run(self, call: object) -> None:
        """Runs a call; a refused call must leave the state exactly as it was."""
        before = self.m.state()
        try:
            call()  # type: ignore[operator]
        except LifoError:
            assert self.m.state() == before

    @rule(account=who, share=st.integers(min_value=0, max_value=2000), dt=step)
    def buy(self, account: str, share: int, dt: int) -> None:
        now = self.advance(dt)
        deposit = self.m.params.virtual_near * share // 1000

        def call() -> None:
            self.bought += self.m.buy(account, deposit, now)
            self.deposited += deposit

        self.run(call)

    @rule(account=who, share=fraction, dt=step)
    def request_sell(self, account: str, share: int, dt: int) -> None:
        now = self.advance(dt)
        self.run(lambda: self.m.request_sell(account, max(self.m.balance(account) * share // 1000, 1), now))

    @rule(sender=who, receiver=who, share=fraction, dt=step)
    def transfer(self, sender: str, receiver: str, share: int, dt: int) -> None:
        now = self.advance(dt)
        self.run(lambda: self.m.transfer(sender, receiver, max(self.m.balance(sender) * share // 1000, 1), now))

    @rule(sender=who, receiver=who, share=fraction, unused=fraction, dt=step)
    def transfer_call(self, sender: str, receiver: str, share: int, unused: int, dt: int) -> None:
        now = self.advance(dt)
        amount = max(self.m.balance(sender) * share // 1000, 1)
        self.run(lambda: self.m.transfer_call(sender, receiver, amount, amount * unused // 1000, now))

    @rule(account=who, dt=step)
    def claim(self, account: str, dt: int) -> None:
        now = self.advance(dt)

        def call() -> None:
            for c in self.m.claim(account, now):
                assert c.request not in self.claimed_ids, "a request was paid twice"
                self.claimed_ids.add(c.request)

        self.run(call)

    @rule(dt=step)
    def settle(self, dt: int) -> None:
        self.run(lambda: self.m.settle(self.advance(dt)))

    # --- invariants ---

    @invariant()
    def tokens_are_conserved(self) -> None:
        assert sum(self.m.balance(a) for a in self.m.lots) + locked_tokens(self.m) == self.m.supply

    @invariant()
    def near_is_conserved(self) -> None:
        claimed = sum(self.m.paid_out.values())
        assert self.m.reserve + self.m.unclaimed_near == self.deposited - claimed
        assert self.m.unclaimed_near == sum(s.paid_left for s in self.m.settlements.values())

    @invariant()
    def the_pool_is_solvent(self) -> None:
        p = self.m.params
        assert self.m.k >= p.virtual_near * p.virtual_tokens
        assert sell_gross(self.m.x, self.m.y, self.m.supply) <= self.m.reserve
        assert self.m.reserve >= 0 and self.m.supply <= p.max_supply

    @invariant()
    def every_settlement_respects_the_gate_priority_and_calm(self) -> None:
        for e, s in self.m.settlements.items():
            if e in self.checked:
                continue
            self.checked.add(e)
            assert s.paid <= s.cap, "the gate"
            event("a settlement where the gate bound" if s.bound else "a settlement under the gate")
            if any(0 < s.filled[v] < s.requested[v] for v in s.requested):
                event("a boundary vintage shared pro rata")
            if s.max_fill is None or s.total_requested <= s.max_fill:
                assert s.filled == s.requested and not s.bound, "calm markets"
            else:
                assert s.total_filled == s.max_fill
            vintages = sorted(s.requested, reverse=True)
            for i, v in enumerate(vintages):
                if s.filled[v] < s.requested[v]:
                    assert all(s.filled[w] == 0 for w in vintages[i + 1 :]), "older vintages wait"
                if s.filled[v] > 0:
                    assert all(s.filled[w] == s.requested[w] for w in vintages[:i]), "newer vintages fill first"

    @invariant()
    def a_fully_claimed_epoch_paid_one_price_and_its_exact_fill(self) -> None:
        for e, s in self.m.settlements.items():
            if s.open_requests:
                continue
            results = [self.m.result(r.id) for r in self.m.requests.values() if r.epoch == e]
            assert sum(c.filled for c in results) == s.total_filled
            for c in results:
                assert c.paid == (c.filled * s.paid // s.total_filled if s.total_filled else 0)
            assert sum(c.paid for c in results) + s.dust_returned == s.paid

    @invariant()
    def no_token_ever_gets_a_newer_vintage(self) -> None:
        """For every vintage v, the tokens of vintage v or newer grow only by what was just bought from the pool."""
        after = tail_counts(self.m)
        for v in set(after) | set(self.tails):
            assert tail_at(after, v) <= tail_at(self.tails, v) + self.bought
        self.tails = after
        self.bought = 0


Market.TestCase.settings = settings(
    max_examples=3 * EXAMPLES,  # about a third of the sequences end without a binding gate
    stateful_step_count=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
test_market = Market.TestCase


# --- The gate's closed form (C3), both sides of the bound -------------------------------------------------------------


@settings(max_examples=EXAMPLES, deadline=None)
@given(
    x=st.integers(min_value=1, max_value=10**30),
    y=st.integers(min_value=1, max_value=10**30),
    cap=st.integers(min_value=0, max_value=10**30),
    fee=st.integers(min_value=0, max_value=9999),
)
def test_the_gate_fill_is_the_exact_largest_amount_under_the_cap(x: int, y: int, cap: int, fee: int) -> None:
    f = gate_fill(x, y, cap, fee)
    if f is None:
        assert sell_paid(x, y, 10**40, fee) <= cap
    else:
        assert f >= 0
        assert sell_paid(x, y, f, fee) <= cap < sell_paid(x, y, f + 1, fee)


# --- Splitting tokens across accounts changes nothing (B6) ------------------------------------------------------------


@settings(max_examples=EXAMPLES, deadline=None)
@given(
    subject_buys=st.lists(st.integers(min_value=10**3, max_value=10**6), min_size=1, max_size=4),
    other_buys=st.lists(st.integers(min_value=10**3, max_value=10**6), min_size=1, max_size=4),
    pieces=st.lists(st.integers(min_value=1, max_value=100), min_size=2, max_size=4),
    sell_share=st.integers(min_value=1, max_value=1000),
    gate=st.sampled_from([50, 200, 500, 2000]),
    order=st.booleans(),
)
def test_splitting_tokens_across_accounts_changes_what_they_are_paid_only_by_rounding(
    subject_buys: list[int], other_buys: list[int], pieces: list[int], sell_share: int, gate: int, order: bool
) -> None:
    p = Params(10**7, 10**9, 9 * 10**8, gate, 100, 10, 10, 0, 16)
    whole, split = Lifo(p), Lifo(p)
    t = 0
    for i in range(max(len(subject_buys), len(other_buys))):
        for m in (whole, split):
            if i < len(subject_buys):
                m.buy("s", subject_buys[i], t)
            if i < len(other_buys):
                m.buy("o", other_buys[i], t)
        t += 10
    total = whole.balance("s")
    names = ["s"] + [f"s{i}" for i in range(1, len(pieces))]
    amounts = [total * w // sum(pieces) for w in pieces]
    amounts[0] += total - sum(amounts)
    for name, amount in zip(names[1:], amounts[1:], strict=True):
        if amount:
            split.transfer("s", name, amount, t)
    other = max(whole.balance("o") * sell_share // 1000, 1)

    def sell_all(m: Lifo, sellers: list[str]) -> None:
        if order:
            m.request_sell("o", other, t)
        for s in sellers:
            if m.balance(s):
                m.request_sell(s, m.balance(s), t)
        if not order:
            m.request_sell("o", other, t)

    sell_all(whole, ["s"])
    sell_all(split, names)
    later = t + 10
    got_whole = whole.claim("s", later)
    got_split = [c for n in names if split.unclaimed_ids.get(n) for c in split.claim(n, later)]
    s = whole.settlements[whole.epoch(t)]
    filled_w, filled_s = sum(c.filled for c in got_whole), sum(c.filled for c in got_split)
    paid_w, paid_s = sum(c.paid for c in got_whole), sum(c.paid for c in got_split)
    n = len(names)
    assert abs(filled_w - filled_s) <= n
    unit_value = -(-s.paid // s.total_filled) if s.total_filled else 0
    assert abs(paid_w - paid_s) <= n * (1 + unit_value)
