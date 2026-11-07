"""The refresh attack, decomposed, and the countermeasures (run 02).

    python -m lifo.refresh --seeds 10 --workers 4      runs everything, writes results/refresh.csv and REFRESH.md
    python -m lifo.refresh --from-csv                   rewrites REFRESH.md and the figure from results/refresh.csv

Three insider strategies are compared under LIFO and FIFO on the same seeds: dump everything at the peak; sell in
slices before the peak and never buy back (12 h and 48 h); refresh (sell in slices, buy back at once from a second
account) and then dump. Each countermeasure is a fixed parameter of the model (no admin). All results are simulated.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from lifo import sim
from lifo.model import DAY, HOUR, MINUTE, NEAR, Lifo, LifoError, default_params
from lifo.sim import REFRESHERS, draw_agents, measures, simulate

OUT = Path(__file__).resolve().parent.parent / "results"
INSIDER_NEAR = 8.0  # what the insider puts in at launch (lifo/sim.py)

# The rules of run 01, written out so that this module does not depend on the defaults of default_params.
V1: dict[str, int] = {
    "gate_bps": 500,
    "epoch_length": 10 * MINUTE,
    "vintage_length": HOUR,
    "sell_fee_bps": 50,
    "buy_fee_bps": 0,
    "young_age": 0,
    "young_fee_bps": 0,
    "min_age": 0,
}
VARIANTS: dict[str, dict[str, int]] = {
    "v1 (run 01 rules)": {},
    "M1 buy fee 50": {"buy_fee_bps": 50},
    "M2 young 6 h, +200": {"young_age": 6 * HOUR, "young_fee_bps": 200},
    "M2 young 24 h, +200": {"young_age": 24 * HOUR, "young_fee_bps": 200},
    "M2 young 24 h, +500": {"young_age": 24 * HOUR, "young_fee_bps": 500},
    "M3 vintage 24 h": {"vintage_length": DAY},
    "M3 vintage 72 h": {"vintage_length": 3 * DAY},
    "M3 vintage 7 d": {"vintage_length": 7 * DAY},
    "M4 min age 1 h": {"min_age": HOUR},
    "M4 min age 6 h": {"min_age": 6 * HOUR},
    "M1 + M2 (24 h, +200)": {"buy_fee_bps": 50, "young_age": 24 * HOUR, "young_fee_bps": 200},
    "M2 (24 h, +200) + M3 24 h": {"young_age": 24 * HOUR, "young_fee_bps": 200, "vintage_length": DAY},
    "M3 72 h + M1": {"vintage_length": 3 * DAY, "buy_fee_bps": 50},
    "M3 72 h + M2 (6 h, +200)": {"vintage_length": 3 * DAY, "young_age": 6 * HOUR, "young_fee_bps": 200},
    "M3 7 d + M1": {"vintage_length": 7 * DAY, "buy_fee_bps": 50},
    "M1 + M2 (24 h, +200) + M3 24 h": {
        "buy_fee_bps": 50,
        "young_age": 24 * HOUR,
        "young_fee_bps": 200,
        "vintage_length": DAY,
    },
}
# The same variants with the peak at day 22, just after a 72 h and a 7 d vintage boundary: the worst case for a coarse vintage.
FINAL = "M3 vintage 24 h"
ROBUST = ("v1 (run 01 rules)", "M3 vintage 24 h", "M3 vintage 72 h", "M3 vintage 7 d")
PEAK_OF: dict[str, int] = {}
for _v in ROBUST:
    VARIANTS[f"{_v} [peak day 22]"] = VARIANTS[_v]
    PEAK_OF[f"{_v} [peak day 22]"] = 22

STRATEGIES = (
    ("a. dump at the peak", "insider_dump"),
    ("b1. slow exit over 12 h", "slow_exit"),
    ("b2. slow exit over 48 h", "slow_exit_48"),
    ("b3. slow exit from the peak, 12 h", "slow_after"),
    ("b4. slow exit from the peak, 48 h", "slow_after_48"),
    ("c1. refresh over 12 h, then dump", "refresh_attack"),
    ("c2. refresh 24 to 36 h early, then dump", "refresh_early"),
)
CROWD = ("calm", "panic", "slow_distribution")  # the other scenarios of the table
SCENARIOS = tuple(s for _, s in STRATEGIES) + CROWD
HOLDS = (1, 6, 24, 72)  # hours a genuine buyer holds before selling, for the round-trip cost
FIELDS = (
    "late_result",
    "insider_result",
    "insider_received_near",
    "early_result",
    "max_1h_drop",
    "refresh_cost_near",
    "crash_share_newest_third",
    "crash_share_oldest_third",
    "oldest_wait_max_h",
)

Job = tuple[str, str, str, int]  # variant, scenario, kind, seed


def params_for(variant: str) -> Any:
    return default_params(**{**V1, **VARIANTS[variant]})


def run_job(job: Job) -> dict[str, Any]:
    variant, scenario, kind, seed = job
    sim.PEAK_DAY = PEAK_OF.get(variant, 20)
    p = params_for(variant)
    groups = draw_agents(scenario, seed, DAY // p.epoch_length).group
    m = measures(simulate(scenario, kind, p, seed), groups)
    return {"variant": variant, "scenario": scenario, "kind": kind, "seed": seed, **{f: m[f] for f in FIELDS}}


def round_trip_cost(variant: str, hold_hours: int) -> float | None:
    """Share of 1 NEAR lost by a buyer who buys at the start of a vintage and sells after `hold_hours`, calm market.

    Includes every fee (buy, sell, young). None when the tokens cannot be requested yet (M4).
    """
    p = params_for(variant)
    m = Lifo(p, transactional=False)
    t0 = p.launch_time + 10 * DAY  # a vintage boundary for every VINTAGE_LENGTH used here
    m.buy("pool", 40 * NEAR, t0)
    got = m.buy("x", NEAR, t0)
    now = t0 + hold_hours * HOUR
    try:
        m.request_sell("x", got, now)
    except LifoError:
        return None
    m.settle(now + p.epoch_length)
    paid = sum(c.paid for c in m.claim("x", now + p.epoch_length))
    return 1 - paid / NEAR


def mean(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return sum(xs) / len(xs) if xs else float("nan")


class Table:
    """Means over seeds and paired per-seed comparisons from the rows of the runs."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.by: dict[tuple[str, str, str], dict[int, dict[str, Any]]] = {}
        for r in rows:
            self.by.setdefault((r["variant"], r["scenario"], r["kind"]), {})[int(r["seed"])] = r
        self.seeds = sorted({int(r["seed"]) for r in rows})

    def m(self, variant: str, scenario: str, kind: str, field: str) -> float:
        return mean([float(r[field]) for r in self.by[(variant, scenario, kind)].values()])

    def insider_total(self, variant: str, scenario: str, kind: str) -> float:
        """NEAR the insider got out plus what it still holds, valued at the final price after fees."""
        return self.m(variant, scenario, kind, "insider_result") * INSIDER_NEAR

    def cash(self, variant: str, scenario: str, kind: str) -> float:
        return self.m(variant, scenario, kind, "insider_received_near")

    def wins(self, variant: str, scenario: str) -> int:
        """Seeds on which the late group does better under LIFO than under FIFO."""
        lifo, fifo = self.by[(variant, scenario, "lifo")], self.by[(variant, scenario, "fifo")]
        return sum(float(lifo[s]["late_result"]) > float(fifo[s]["late_result"]) for s in self.seeds)

    def best_strategy(self, variant: str) -> tuple[str, str, float]:
        best = max(STRATEGIES, key=lambda t: self.insider_total(variant, t[1], "lifo"))
        return best[0], best[1], self.insider_total(variant, best[1], "lifo")

    def paired_late(self, variant: str, a: str, b: str) -> int:
        """Seeds on which the late group (LIFO) gets less when the insider plays `a` than when it plays `b`."""
        x, y = self.by[(variant, a, "lifo")], self.by[(variant, b, "lifo")]
        return sum(float(x[s]["late_result"]) < float(y[s]["late_result"]) for s in self.seeds)

    def paired_insider(self, variant: str, a: str, b: str) -> int:
        """Seeds on which the insider (LIFO) ends with more NEAR playing `a` than playing `b`."""
        x, y = self.by[(variant, a, "lifo")], self.by[(variant, b, "lifo")]
        return sum(float(x[s]["insider_result"]) > float(y[s]["insider_result"]) for s in self.seeds)


def f(v: float, nd: int = 2) -> str:
    return "n/a" if v is None or math.isnan(v) else f"{v:.{nd}f}"


def decomposition_md(t: Table, variant: str) -> list[str]:
    n = len(t.seeds)
    lines = [
        f"| insider strategy | insider LIFO: cash out | insider LIFO: total | insider FIFO: total | late group LIFO | late group FIFO | seeds where LIFO is better for the late group (of {n}) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, sc in STRATEGIES:
        lines.append(
            f"| {name} | {f(t.cash(variant, sc, 'lifo'))} | {f(t.insider_total(variant, sc, 'lifo'))} "
            f"| {f(t.insider_total(variant, sc, 'fifo'))} | {f(t.m(variant, sc, 'lifo', 'late_result'), 3)} "
            f"| {f(t.m(variant, sc, 'fifo', 'late_result'), 3)} | {t.wins(variant, sc)} |"
        )
    return lines


def write_md(rows: list[dict[str, Any]], meta: str) -> str:
    t = Table(rows)
    n = len(t.seeds)
    names = list(VARIANTS)
    base = names[0]
    out = ["# The refresh attack: decomposition and countermeasures\n", meta]
    notes = OUT / "refresh_findings.md"
    if notes.exists():
        out.append(notes.read_text(encoding="utf-8"))
    out.append(
        f"\n## 1. Decomposition at the run 01 rules ({base})\n\nGate 500, epoch 10 min, vintage 1 h, sell fee 50. The insider put "
        f"in {INSIDER_NEAR:.0f} NEAR at launch. Means over {n} seeds, same agents and seeds for every row. "
        '"Cash out" is the NEAR the insider received; "total" adds what it still holds, valued at the final price '
        "net of the sell fee. In strategies b1, b2 and c the insider sells in slices of equal size from 12 h (or 48 h) "
        "before the peak until it holds nothing (b) or, in c, buys back with each payout from a second account and dumps "
        "everything at the peak.\n"
    )
    out += decomposition_md(t, base)
    out.append("\nPaired per seed, LIFO:\n")
    for a, b, label in (
        ("refresh_attack", "slow_exit", "refresh vs slow exit over 12 h"),
        ("refresh_attack", "slow_exit_48", "refresh vs slow exit over 48 h"),
        ("refresh_attack", "insider_dump", "refresh vs plain dump"),
        ("refresh_early", "slow_exit_48", "early refresh (c2) vs slow exit over 48 h"),
        ("refresh_early", "refresh_attack", "early refresh (c2) vs late refresh (c1)"),
    ):
        out.append(
            f"- {label}: the insider ends with more NEAR in the refresh on {t.paired_insider(base, a, b)} of {n} seeds; "
            f"the late group does worse in the refresh on {t.paired_late(base, a, b)} of {n} seeds."
        )
    out.append(f"\n## 2. Countermeasures ({n} seeds, LIFO unless said otherwise; FIFO has the same fees)\n")
    out.append(
        "Insider: total NEAR (cash plus holdings) under each strategy, the best one, and the refresh minus the best "
        "honest strategy (a, b1 to b4); c1 and c2 are the two refreshes. Positive means the refresh pays more than any honest exit.\n"
    )
    names_s = [name.split(". ")[0] for name, _ in STRATEGIES]
    out.append("| countermeasure | " + " | ".join(names_s) + " | best strategy | best refresh minus best honest |")
    out.append("|---|" + "---|" * (len(STRATEGIES) + 2))
    for v in names:
        tot = {sc: t.insider_total(v, sc, "lifo") for _, sc in STRATEGIES}
        honest = max(x for sc, x in tot.items() if sc not in REFRESHERS)
        refresh = max(x for sc, x in tot.items() if sc in REFRESHERS)
        best = t.best_strategy(v)[0].split(".")[0]
        out.append(f"| {v} | " + " | ".join(f(x) for x in tot.values()) + f" | {best} | {refresh - honest:+.2f} |")
    out.append(
        f"\nLate group, NEAR back per NEAR invested, LIFO (FIFO) and the number of seeds out of {n} where LIFO is better:\n"
    )
    out.append(
        "| countermeasure | insider dump | panic | calm growth | refresh c1 | refresh c2 | insider's best strategy: late group |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for v in names:
        cells = []
        for sc in ("insider_dump", "panic", "calm", "refresh_attack", "refresh_early"):
            cells.append(
                f"{f(t.m(v, sc, 'lifo', 'late_result'), 3)} ({f(t.m(v, sc, 'fifo', 'late_result'), 3)}), {t.wins(v, sc)}/{n}"
            )
        _, sc, _ = t.best_strategy(v)
        cells.append(f"{f(t.m(v, sc, 'lifo', 'late_result'), 3)} ({f(t.m(v, sc, 'fifo', 'late_result'), 3)})")
        out.append(f"| {v} | " + " | ".join(cells) + " |")
    out.append(
        "\nWhat a genuine buyer pays: share of 1 NEAR lost in a round trip in a calm market (buy at the start of a "
        'vintage, sell after the time shown; every fee counted). "locked" means the tokens cannot be requested yet.\n'
    )
    out.append("| countermeasure | " + " | ".join(f"sold after {h} h" for h in HOLDS) + " |")
    out.append("|---|" + "---|" * len(HOLDS))
    for v in names:
        cells = []
        for h in HOLDS:
            c = round_trip_cost(v, h)
            cells.append("locked" if c is None else f"{100 * c:.2f} %")
        out.append(f"| {v} | " + " | ".join(cells) + " |")
    out.append(
        "\nLargest one-hour price drop and early buyers' result in the insider dump, LIFO:\n\n"
        "| countermeasure | 1 h drop | early buyers per NEAR |\n|---|---|---|"
    )
    for v in names:
        out.append(
            f"| {v} | {f(t.m(v, 'insider_dump', 'lifo', 'max_1h_drop'))} | {f(t.m(v, 'insider_dump', 'lifo', 'early_result'), 3)} |"
        )
    return "\n".join(out) + "\n"


def figure(rows: list[dict[str, Any]], final: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from lifo.figures import FIFO_STYLE, LIFO_STYLE, save

    t = Table(rows)
    base = next(iter(VARIANTS))
    series = (
        ("FIFO baseline", FIFO_STYLE["color"], base, "fifo"),
        ("LIFO, run 01 rules", "#e6a29a", base, "lifo"),
        ("LIFO, recommended", LIFO_STYLE["color"], final, "lifo"),
    )
    labels = ["dump", "slow\n-12 h", "slow\n-48 h", "slow\n+12 h", "slow\n+48 h", "refresh\n12 h", "refresh\n36 h"]
    n = len(labels)
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 5.6), sharex=True)
    width = 0.27
    for k, (name, color, variant, kind) in enumerate(series):
        xs = [i + (k - 1) * width for i in range(n)]
        axes[0].bar(xs, [t.insider_total(variant, sc, kind) for _, sc in STRATEGIES], width, color=color, label=name)
        axes[1].bar(xs, [t.m(variant, sc, kind, "late_result") for _, sc in STRATEGIES], width, color=color)
    axes[0].set_ylabel("insider's NEAR\n(8 NEAR put in)")
    axes[1].set_ylabel("late group, NEAR back\nper NEAR invested")
    axes[1].set_xticks(range(n), labels)
    axes[0].legend(frameon=False, ncol=3, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    save(fig, "refresh_decomposition.png")


def git_commit() -> str:
    from lifo.sweep import git_commit as commit

    return commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh attack decomposition and countermeasures.")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--from-csv", action="store_true")
    parser.add_argument("--final", default=FINAL, help="the recommended variant")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("at most 4 workers on this machine")
    path = OUT / "refresh.csv"
    runtime = float("nan")
    if args.from_csv:
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        seeds = len({r["seed"] for r in rows})
    else:
        seeds = args.seeds
        jobs: list[Job] = [
            (v, sc, kind, seed)
            for v in VARIANTS
            for sc in SCENARIOS
            for kind in ("fifo", "lifo")
            for seed in range(1, seeds + 1)
        ]
        started = time.perf_counter()
        with Pool(args.workers) as pool:
            rows = pool.map(run_job, jobs, chunksize=4)
        runtime = time.perf_counter() - started
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    command = f"python -m lifo.refresh --seeds {seeds} --workers {args.workers}"
    meta = (
        f"Simulated, not market data. Command: `{command}`. Seeds 1 to {seeds}. Commit `{git_commit()}`. "
        f"Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots. "
        f"Runs: {len(rows)}, in `results/refresh.csv`"
        + ("" if math.isnan(runtime) else f"; runtime {runtime:.0f} s")
        + ".\n"
    )
    (OUT / "REFRESH.md").write_text(write_md(rows, meta), encoding="utf-8", newline="\n")
    if args.final in VARIANTS:
        figure(rows, args.final)
    print(f"{len(rows)} rows; REFRESH.md written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
