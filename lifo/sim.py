"""Agent-based simulation of 60 days on the model (S1 to S4): the same agents and seed under FIFO and under LIFO.

    python -m lifo.sim --scenario insider_dump --seed 1     one pair of runs, printed as a table

Agents decide with floats (arrival times, sizes, panic thresholds); every balance and payout is the model's integers.
The FIFO baseline is the same curve and the same sell fee, with sells executed at once, in arrival order, no gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

from lifo.model import DAY, HOUR, NEAR, TOKEN, Lifo, LifoError, Params, default_params, sell_paid

DAYS = 60
PEAK_DAY = 20  # the late wave buys in the three days before it; the dump, the shock or nothing happens then
CRASH_WINDOW = 2 * DAY  # payouts by vintage are measured over this window after the peak
QUEUE_FEAR = 0.75  # assumed: when the last batch hit the gate, holders panic at 75 % of their usual threshold
SCENARIOS = ("calm", "insider_dump", "panic", "slow_distribution", "refresh_attack")
GROUPS = ("insider", "early", "retail", "late")


def to_yocto(near: float) -> int:
    return round(near * 1e6) * 10**18


# --- Markets ---------------------------------------------------------------------------------------------------------


class FifoMarket(Lifo):
    """The baseline: the same curve and fee, every sell executed at once in arrival order, no gate, no batch."""

    def sell_now(self, account: str, amount: int) -> tuple[int, list[tuple[int, int]]]:
        portions = self._take(account, amount)
        paid = sell_paid(self.x, self.y, amount, self.params.sell_fee_bps)
        self.reserve -= paid
        self.supply -= amount
        self.paid_out[account] = self.paid_out.get(account, 0) + paid
        return paid, portions


@dataclass
class Agents:
    """Every agent drawn once from the seed, before any market runs, so FIFO and LIFO face the same people."""

    group: list[str]
    arrive: np.ndarray  # step of the buy
    size: np.ndarray  # NEAR
    theta: np.ndarray  # panic threshold: sells everything when the price falls this far below its peak since entry
    hold_until: np.ndarray  # step at which it sells anyway (np.inf: never)

    @property
    def n(self) -> int:
        return len(self.group)


def draw_agents(scenario: str, seed: int, steps_per_day: int) -> Agents:
    rng = np.random.default_rng(seed)
    group: list[str] = []
    arrive: list[float] = []
    size: list[float] = []
    theta: list[float] = []
    hold: list[float] = []

    def add(g: str, day: float, near: float, th: float, hold_days: float) -> None:
        group.append(g)
        arrive.append(int(day * steps_per_day))
        size.append(near)
        patient = scenario == "calm" and not np.isinf(
            th
        )  # calm growth: holders sell on their own clock, rarely in fear
        theta.append(min(2 * th, 0.95) if patient else th)
        hold.append(np.inf if np.isinf(hold_days) else int((day + hold_days) * steps_per_day))

    add("insider", 0.0, 8.0, np.inf, np.inf)  # buys in the first epoch, never panics
    for _ in range(20):
        add("early", rng.uniform(0, 0.25), rng.lognormal(np.log(1.2), 0.5), rng.uniform(0.4, 0.7), rng.exponential(25))
    growth_rate, after_rate = 12.0, (12.0 if scenario == "calm" else 4.0)
    day = 0.25
    while day < DAYS:
        day += rng.exponential(1 / (growth_rate if day < PEAK_DAY else after_rate))
        if day < DAYS:
            add("retail", day, rng.lognormal(np.log(0.25), 0.8), rng.uniform(0.2, 0.5), rng.exponential(10))
    late_until = DAYS if scenario == "calm" else PEAK_DAY  # in calm growth the same people arrive until the end
    for _ in range(120):
        add(
            "late",
            rng.uniform(PEAK_DAY - 3, late_until),
            rng.lognormal(np.log(0.4), 0.6),
            rng.uniform(0.15, 0.4),
            np.inf,
        )
    return Agents(group, np.array(arrive), np.array(size), np.array(theta), np.array(hold, dtype=float))


# --- One run -----------------------------------------------------------------------------------------------------


@dataclass
class Run:
    scenario: str
    kind: str  # "fifo" or "lifo"
    params: Params
    seed: int
    price: list[float] = field(default_factory=list)
    reserve: list[float] = field(default_factory=list)
    supply: list[float] = field(default_factory=list)
    invested: dict[int, int] = field(default_factory=dict)
    received: dict[int, int] = field(default_factory=dict)
    sold: dict[int, int] = field(default_factory=dict)
    crash_paid_by_vintage: dict[int, float] = field(default_factory=dict)
    waits: dict[str, list[float]] = field(default_factory=dict)
    gate_bound_epochs: int = 0
    refused_buys: int = 0
    refresh_cost: float = 0.0
    final_tokens: dict[int, int] = field(default_factory=dict)


def simulate(scenario: str, kind: str, params: Params, seed: int, queue_fear: float = QUEUE_FEAR) -> Run:
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    steps_per_day = DAY // params.epoch_length
    steps = DAYS * steps_per_day
    agents = draw_agents(scenario, seed, steps_per_day)
    market: Lifo = FifoMarket(params, transactional=False) if kind == "fifo" else Lifo(params, transactional=False)
    run = Run(scenario, kind, params, seed)
    names = [f"{g}{i}" for i, g in enumerate(agents.group)]
    index = {name: i for i, name in enumerate(names)}
    insider = 0
    fresh = "insider_fresh"  # the refresher's second account
    index[fresh] = insider
    peak = np.zeros(agents.n)
    holding = np.zeros(agents.n, dtype=bool)
    selling = np.zeros(agents.n, dtype=bool)
    sell_start = np.full(agents.n, -1)
    group_arr = np.array(agents.group)
    early_mask = group_arr == "early"
    by_step: dict[int, list[int]] = {}
    for i, s in enumerate(agents.arrive):
        by_step.setdefault(int(s), []).append(i)
    peak_step = PEAK_DAY * steps_per_day
    crash_end = peak_step + CRASH_WINDOW // params.epoch_length
    refresh_from = peak_step - 12 * HOUR // params.epoch_length
    slow_every = 6 * HOUR // params.epoch_length
    order_rng = np.random.default_rng([seed, 7])
    pending: set[str] = set()
    lifo = not isinstance(market, FifoMarket)
    last_bound = False
    initial_tokens = np.zeros(agents.n)
    refresh_tokens_before = 0

    def credit(account: str, near: int, tokens: int) -> None:
        i = index[account]
        run.received[i] = run.received.get(i, 0) + near
        run.sold[i] = run.sold.get(i, 0) + tokens

    def holdings(account: str) -> int:
        """Tokens held, including those locked in requests that are not filled yet."""
        total = market.balance(account)
        for r in market.unclaimed_ids.get(account, []):
            req = market.requests[r]
            total += req.amount - (market.result(r).filled if req.epoch in market.settlements else 0)
        return total

    def finish_if_done(i: int, step: int) -> None:
        if holdings(names[i]) + (holdings(fresh) if i == insider else 0) == 0:
            if sell_start[i] >= 0:
                run.waits.setdefault(agents.group[i], []).append((step - sell_start[i]) * params.epoch_length / HOUR)
            selling[i] = False
            holding[i] = False
            sell_start[i] = -1

    def sell(account: str, amount: int, step: int, now: int) -> None:
        if amount <= 0:
            return
        if lifo:
            market.request_sell(account, amount, now)
            pending.add(account)
        else:
            assert isinstance(market, FifoMarket)
            paid, portions = market.sell_now(account, amount)
            credit(account, paid, amount)
            if step >= peak_step and step < crash_end:
                for v, a in portions:
                    run.crash_paid_by_vintage[v] = run.crash_paid_by_vintage.get(v, 0.0) + paid * a / amount / NEAR

    for step in range(steps):
        now = params.launch_time + step * params.epoch_length
        # 1. LIFO: the first call of the epoch settles the last one; every owner claims.
        if lifo and pending:
            before = set(market.settlements)
            market.settle(now)
            for e in set(market.settlements) - before:
                s = market.settlements[e]
                last_bound = s.bound
                run.gate_bound_epochs += int(s.bound)
                if peak_step <= step < crash_end and s.total_filled:
                    for v, f in s.filled.items():
                        run.crash_paid_by_vintage[v] = (
                            run.crash_paid_by_vintage.get(v, 0.0) + s.paid * f / s.total_filled / NEAR
                        )
            for account in sorted(pending):
                try:
                    claims = market.claim(account, now)
                except LifoError:
                    continue
                credit(account, sum(c.paid for c in claims), sum(c.filled for c in claims))
            pending = {a for a in pending if market.unclaimed_ids.get(a)}
        price = (market.x / NEAR) / (market.y / TOKEN)
        # 2. Arrivals buy.
        for i in by_step.get(step, []):
            deposit = to_yocto(float(agents.size[i]))
            out = market.quote_buy(deposit)
            if out <= 0 or market.supply + out > params.max_supply:
                run.refused_buys += 1
                continue
            got = market.buy(names[i], deposit, now)
            run.invested[i] = run.invested.get(i, 0) + deposit
            initial_tokens[i] += got
            holding[i] = True
            peak[i] = max(peak[i], price)
        # 3. Who wants out this epoch.
        peak = np.where(holding, np.maximum(peak, price), peak)
        fear = queue_fear if (lifo and last_bound) else 1.0
        theta = agents.theta * fear
        if scenario == "panic" and peak_step <= step < peak_step + steps_per_day:
            theta = theta * 0.5
        want = holding & ~selling & ((price < peak * (1 - theta)) | (agents.hold_until <= step))
        if step == peak_step:
            if scenario in ("insider_dump", "refresh_attack"):
                want[insider] = True
            if scenario == "panic":
                rumour = np.flatnonzero(early_mask)[::2]
                want[rumour] = holding[rumour]
        for i in np.flatnonzero(want).tolist():
            selling[i] = True
            sell_start[i] = step
        # 4. Scenario actions that are not "sell everything".
        if scenario == "slow_distribution" and step >= 10 * steps_per_day and step % slow_every == 0:
            for i in np.flatnonzero((group_arr == "early") | (group_arr == "insider")).tolist():
                if holding[i] and not selling[i]:
                    sell(names[i], min(market.balance(names[i]), int(initial_tokens[i] * 0.02)), step, now)
        if scenario == "refresh_attack" and refresh_from <= step < peak_step:
            if step == refresh_from:
                refresh_tokens_before = holdings(names[insider])
            # In slices over 12 hours from the old account; each payout buys at once into a second account.
            chunk = refresh_tokens_before * 3 // (2 * (peak_step - refresh_from))
            sell(names[insider], min(market.balance(names[insider]), chunk), step, now)
            got_near = run.received.pop(insider, 0)
            if got_near:
                run.sold.pop(insider, None)
                market.buy(fresh, got_near, now)
            if step == peak_step - 1:
                lost = refresh_tokens_before - holdings(names[insider]) - holdings(fresh)
                run.refresh_cost = lost * price / TOKEN
        # 5. Sellers ask for everything they hold, every epoch until they are out (no carry-over in LIFO).
        sellers = np.flatnonzero(selling).tolist()
        order_rng.shuffle(sellers)
        for i in sellers:
            for account in [names[i], fresh] if i == insider else [names[i]]:
                sell(account, market.balance(account), step, now)
            if not lifo:
                finish_if_done(i, step)
        if lifo:
            for i in sellers:
                finish_if_done(i, step)
        run.price.append(price)
        run.reserve.append(market.reserve / NEAR)
        run.supply.append(market.supply / TOKEN)
    for i, name in enumerate(names):
        run.final_tokens[i] = holdings(name)
    run.final_tokens[insider] += holdings(fresh)
    return run


# --- Measures (S4) -------------------------------------------------------------------------------------------------


def measures(run: Run, agents_group: list[str]) -> dict[str, float]:
    p = run.params
    final_price = run.price[-1]
    fee = p.sell_fee_bps / 10_000

    def group_result(g: str) -> float:
        ids = [i for i, gg in enumerate(agents_group) if gg == g and i in run.invested]
        inv = sum(run.invested[i] for i in ids)
        if not inv:
            return float("nan")
        got = sum(run.received.get(i, 0) for i in ids)
        held = sum(run.final_tokens.get(i, 0) for i in ids) * final_price / TOKEN * (1 - fee) * NEAR
        return (got + held) / inv

    def exit_price(g: str) -> float:
        ids = [i for i, gg in enumerate(agents_group) if gg == g]
        tokens = sum(run.sold.get(i, 0) for i in ids)
        return sum(run.received.get(i, 0) for i in ids) / NEAR / (tokens / TOKEN) if tokens else float("nan")

    prices = np.array(run.price)
    w = max(1, HOUR // p.epoch_length)
    windows = np.lib.stride_tricks.sliding_window_view(prices, w + 1)
    drop = float(np.max(1 - windows.min(axis=1) / windows[:, 0]))
    peak_vintage = (PEAK_DAY * DAY) // p.vintage_length
    third = peak_vintage / 3
    paid = run.crash_paid_by_vintage
    total = sum(paid.values()) or float("nan")
    newest = sum(a for v, a in paid.items() if v >= 2 * third) / total
    oldest = sum(a for v, a in paid.items() if v < third) / total
    waits = run.waits.get("early", []) + run.waits.get("insider", [])
    return {
        "late_result": group_result("late"),
        "retail_result": group_result("retail"),
        "early_result": group_result("early"),
        "insider_result": group_result("insider"),
        "early_exit_price": exit_price("early"),
        "insider_exit_price": exit_price("insider"),
        "max_1h_drop": drop,
        "crash_paid_near": sum(paid.values()),
        "crash_share_newest_third": newest,
        "crash_share_oldest_third": oldest,
        "oldest_wait_mean_h": float(np.mean(waits)) if waits else 0.0,
        "oldest_wait_max_h": float(np.max(waits)) if waits else 0.0,
        "gate_bound_epochs": float(run.gate_bound_epochs),
        "refused_buys": float(run.refused_buys),
        "refresh_cost_near": run.refresh_cost,
        "insider_received_near": run.received.get(0, 0) / NEAR,
        "final_price": final_price,
        "final_reserve": run.reserve[-1],
        "peak_reserve": max(run.reserve),
    }


def pair(scenario: str, params: Params, seed: int) -> dict[str, dict[str, float]]:
    steps_per_day = DAY // params.epoch_length
    groups = draw_agents(scenario, seed, steps_per_day).group
    return {kind: measures(simulate(scenario, kind, params, seed), groups) for kind in ("fifo", "lifo")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One scenario, FIFO and LIFO on the same seed.")
    parser.add_argument("--scenario", choices=SCENARIOS, default="insider_dump")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    result = pair(args.scenario, default_params(), args.seed)
    print(f"{'measure':28} {'fifo':>12} {'lifo':>12}")
    for key in result["fifo"]:
        print(f"{key:28} {result['fifo'][key]:12.4g} {result['lifo'][key]:12.4g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
