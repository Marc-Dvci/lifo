"""The headline comparison, controlled: LIFO's gate, rounds and fee held fixed while only the payment order changes.

    python -m lifo.controls --seeds 10 --workers 4      runs everything, writes results/controls.csv and CONTROLS.md
    python -m lifo.controls --from-csv                  rewrites CONTROLS.md and the figure from results/controls.csv

The FIFO baseline of the main tables sells at once with no gate, so it differs from LIFO in three ways: the cap of
5 % of the reserve per round, the round itself (one price per batch) and the order (newest vintage first). Five rules
on the same agents and seeds separate the three (lifo/sim.py, KINDS):

    fifo              no cap, no round: every sell at once, in arrival order (the baseline of the main tables)
    cap               the cap, no round: sells at once in arrival order until 5 % of the reserve has left this round
    cap_round         the cap and the round, every vintage filled in the same proportion
    cap_round_oldest  the cap and the round, oldest vintage first (the mirror of LIFO)
    lifo              the cap and the round, newest vintage first (the rule in the contract)

Every capped rule gets the same queue-fear assumption as LIFO. The insider also plays each of its seven strategies
under each rule, so each rule is measured against the insider's best strategy for that rule. All results are simulated.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from lifo.model import DAY, default_params
from lifo.refresh import INSIDER_NEAR
from lifo.refresh import STRATEGIES as REFRESH_STRATEGIES
from lifo.sim import KINDS, draw_agents, measures, simulate
from lifo.sweep import git_commit

OUT = Path(__file__).resolve().parent.parent / "results"
# The seven strategies of the refresh study, and one that only matters without rounds: being first in every round.
STRATEGIES = REFRESH_STRATEGIES + (("d. dump at the peak, first in every round", "dump_first"),)
RULES = tuple(KINDS)
LABELS = {
    "fifo": "FIFO, at once (main baseline)",
    "cap": "cap only, at once",
    "cap_round": "cap + rounds, pro rata",
    "cap_round_oldest": "cap + rounds, oldest first",
    "lifo": "LIFO: cap + rounds, newest first",
}
CROWD = ("calm", "insider_dump", "panic", "slow_distribution", "refresh_attack")  # the rows of the main table
SCENARIOS = tuple(dict.fromkeys(CROWD + tuple(s for _, s in STRATEGIES)))
FIELDS = (
    "late_result",
    "early_result",
    "insider_result",
    "max_1h_drop",
    "oldest_wait_mean_h",
    "crash_share_newest_third",
    "gate_bound_epochs",
)

Job = tuple[str, str, int]  # scenario, rule, seed


def run_job(job: Job) -> dict[str, Any]:
    scenario, rule, seed = job
    p = default_params()
    groups = draw_agents(scenario, seed, DAY // p.epoch_length).group
    m = measures(simulate(scenario, rule, p, seed), groups)
    return {"scenario": scenario, "rule": rule, "seed": seed, **{f: m[f] for f in FIELDS}}


def mean(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return sum(xs) / len(xs) if xs else float("nan")


class Table:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.by: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
        for r in rows:
            self.by.setdefault((r["scenario"], r["rule"]), {})[int(r["seed"])] = r
        self.seeds = sorted({int(r["seed"]) for r in rows})

    def m(self, scenario: str, rule: str, field: str) -> float:
        return mean([float(r[field]) for r in self.by[(scenario, rule)].values()])

    def insider_total(self, scenario: str, rule: str) -> float:
        return self.m(scenario, rule, "insider_result") * INSIDER_NEAR

    def best(self, rule: str) -> tuple[str, str]:
        """The insider strategy that leaves the insider with the most NEAR under `rule` (mean over seeds)."""
        name, scenario = max(STRATEGIES, key=lambda t: self.insider_total(t[1], rule))
        return name, scenario

    def wins(self, scenario: str, a: str, b: str, field: str = "late_result") -> int:
        x, y = self.by[(scenario, a)], self.by[(scenario, b)]
        return sum(float(x[s][field]) > float(y[s][field]) for s in self.seeds)

    def wins_best(self, a: str, b: str) -> int:
        """Seeds on which the late group does better under `a` than under `b`, each facing the insider's best."""
        x, y = self.by[(self.best(a)[1], a)], self.by[(self.best(b)[1], b)]
        return sum(float(x[s]["late_result"]) > float(y[s]["late_result"]) for s in self.seeds)


def f(v: float, nd: int = 3) -> str:
    return "n/a" if math.isnan(v) else f"{v:.{nd}f}"


def write_md(rows: list[dict[str, Any]], meta: str) -> str:
    t = Table(rows)
    n = len(t.seeds)
    head = "| scenario | " + " | ".join(LABELS[r] for r in RULES) + " |"
    rule_line = "|---" * (len(RULES) + 1) + "|"
    out = [
        "# Controls: the same gate, rounds and fee, only the order changed",
        "",
        meta,
        "Launch rules for every capped rule: cap 500 bps of the reserve per round, round 10 min, vintage 24 h, sell fee"
        " 50 bps. The FIFO baseline has the same curve and fee. Every capped rule uses the same queue-fear assumption"
        " as LIFO (holders panic at 75 % of their usual threshold after a round where the cap was reached).",
        "",
        "## 1. Late group, NEAR back per NEAR invested",
        "",
        f"Mean of {n} seeds, the same agents and seeds in every column.",
        "",
        head,
        rule_line,
    ]
    for sc in CROWD:
        out.append(f"| {sc} | " + " | ".join(f(t.m(sc, r, "late_result")) for r in RULES) + " |")
    best = {r: t.best(r) for r in RULES}
    out.append(
        "| **insider's best strategy for each rule** | "
        + " | ".join(f"{f(t.m(best[r][1], r, 'late_result'))} ({best[r][0].split('.')[0]})" for r in RULES)
        + " |"
    )
    out += [
        "",
        "The last row lets the insider pick, for each rule, the strategy of section 3 that leaves it with the most NEAR"
        " (the letter in brackets).",
        "",
        "## 2. Where LIFO's gain over the FIFO baseline comes from",
        "",
        "Each step adds one thing to the rule before it. Differences of the means above.",
        "",
        "| scenario | the cap (cap only minus FIFO) | rounds (cap + rounds, pro rata, minus cap only) |"
        " newest first (LIFO minus cap + rounds, pro rata) | LIFO minus FIFO |"
        f" seeds where LIFO beats cap + rounds, pro rata (of {n}) |",
        "|---|---|---|---|---|---|",
    ]
    for sc in CROWD:
        fifo, cap, rnd, lifo = (t.m(sc, r, "late_result") for r in ("fifo", "cap", "cap_round", "lifo"))
        out.append(
            f"| {sc} | {cap - fifo:+.3f} | {rnd - cap:+.3f} | {lifo - rnd:+.3f} | {lifo - fifo:+.3f}"
            f" | {t.wins(sc, 'lifo', 'cap_round')} |"
        )
    bf, bc, br, bl = (t.m(best[r][1], r, "late_result") for r in ("fifo", "cap", "cap_round", "lifo"))
    out.append(
        f"| insider's best strategy for each rule | {bc - bf:+.3f} | {br - bc:+.3f} | {bl - br:+.3f} | {bl - bf:+.3f}"
        f" | {t.wins_best('lifo', 'cap_round')} |"
    )
    out += [
        "",
        "## 3. The insider under each rule",
        "",
        f"NEAR the insider ends with (8 NEAR put in at launch), cash plus what it still holds at the final price. Mean of"
        f" {n} seeds. The largest value in each column is the insider's best strategy for that rule.",
        "",
        "| insider strategy | " + " | ".join(LABELS[r] for r in RULES) + " |",
        rule_line,
    ]
    for name, sc in STRATEGIES:
        cells = []
        for r in RULES:
            v = f(t.insider_total(sc, r), 2)
            cells.append(f"**{v}**" if best[r][1] == sc else v)
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    out += [
        "",
        "The late group's result under each of those strategies:",
        "",
        "| insider strategy | " + " | ".join(LABELS[r] for r in RULES) + " |",
        rule_line,
    ]
    for name, sc in STRATEGIES:
        out.append(f"| {name} | " + " | ".join(f(t.m(sc, r, "late_result")) for r in RULES) + " |")
    out += [
        "",
        "## 4. Other measures, insider dump at the peak",
        "",
        "| measure | " + " | ".join(LABELS[r] for r in RULES) + " |",
        rule_line,
        "| early buyers, NEAR back per NEAR | "
        + " | ".join(f(t.m("insider_dump", r, "early_result")) for r in RULES)
        + " |",
        "| largest one-hour price drop | "
        + " | ".join(f"{t.m('insider_dump', r, 'max_1h_drop') * 100:.0f} %" for r in RULES)
        + " |",
        "| share of the crash payouts to the newest third of vintages | "
        + " | ".join(f"{t.m('insider_dump', r, 'crash_share_newest_third') * 100:.0f} %" for r in RULES)
        + " |",
        "| oldest holders' mean wait for their last fill (hours) | "
        + " | ".join(f(t.m("insider_dump", r, "oldest_wait_mean_h"), 1) for r in RULES)
        + " |",
        "| rounds where the cap was reached | "
        + " | ".join(f(t.m("insider_dump", r, "gate_bound_epochs"), 0) for r in RULES)
        + " |",
        "",
    ]
    return "\n".join(out)


def figure(rows: list[dict[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from lifo.figures import save

    t = Table(rows)
    colors = {
        "fifo": "#9a9a9a",
        "cap": "#c9c9c9",
        "cap_round": "#7fa7c9",
        "cap_round_oldest": "#3c6e99",
        "lifo": "#c0392b",
    }
    groups = [
        ("insider\ndump", "insider_dump"),
        ("panic", "panic"),
        ("slow\ndistribution", "slow_distribution"),
        ("calm", "calm"),
    ]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    width = 0.16
    for k, r in enumerate(RULES):
        xs = [i + (k - 2) * width for i in range(len(groups))]
        ys = [t.m(sc, r, "late_result") for _, sc in groups]
        ax.bar(xs, ys, width, color=colors[r], label=LABELS[r])
    ax.set_xticks(range(len(groups)), [g for g, _ in groups])
    ax.set_ylabel("late group, NEAR back\nper NEAR invested")
    ax.axhline(1.0, color="#dddddd", lw=0.8, zorder=0)
    ax.legend(frameon=False, ncol=3, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    save(fig, "controls.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LIFO against control rules with the same gate, rounds and fee.")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--from-csv", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("at most 4 workers on this machine")
    path = OUT / "controls.csv"
    runtime = float("nan")
    if args.from_csv:
        with path.open(encoding="utf-8") as fh:
            rows: list[dict[str, Any]] = list(csv.DictReader(fh))
        seeds = len({r["seed"] for r in rows})
    else:
        seeds = args.seeds
        jobs: list[Job] = [(sc, r, s) for sc in SCENARIOS for r in RULES for s in range(1, seeds + 1)]
        started = time.perf_counter()
        with Pool(args.workers) as pool:
            rows = pool.map(run_job, jobs, chunksize=2)
        runtime = time.perf_counter() - started
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    command = f"python -m lifo.controls --seeds {seeds} --workers {args.workers}"
    meta = (
        f"Simulated. Command: `{command}`. Seeds 1 to {seeds}. Commit `{git_commit()}`. Runs: {len(rows)}, in"
        f" `results/controls.csv`" + ("" if math.isnan(runtime) else f"; runtime {runtime:.0f} s") + "."
    )
    (OUT / "CONTROLS.md").write_text(write_md(rows, meta), encoding="utf-8", newline="\n")
    figure(rows)
    print(f"{len(rows)} runs; CONTROLS.md and figures/controls.png written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
