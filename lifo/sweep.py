"""The parameter sweep (S5) and the result files (S6).

    python -m lifo.sweep --seeds 10 --workers 4      (about 9 minutes on 4 workers)

Writes results/sweep.csv (every run), results/<scenario>.md (one table per scenario), results/SWEEP.md and the
figures in results/figures/. Every number in those files comes from this command; all of it is simulated.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from lifo.model import DAY, HOUR, MINUTE, default_params
from lifo.sim import QUEUE_FEAR, SCENARIOS, draw_agents, measures, simulate

OUT = Path(__file__).resolve().parent.parent / "results"
GATES = (200, 500, 1000, 2000)
EPOCHS = (5, 10, 30)  # minutes
VINTAGES = (1, 6, 24)  # hours
FEES = (50, 100, 200, 300)
LAUNCH = (500, 10, 24, 50)  # the launch rules (requirements-v2 S5'): tables, figures and sensitivity are drawn here
CALM_TOLERANCE = 0.01  # calm growth counts as "not worse" if the late group loses at most 1 cent per NEAR invested
RANK_BAND = 0.02  # late-group gains closer than this to the best are ranked by the price drop instead

Config = tuple[int, int, int, int]  # gate bps, epoch minutes, vintage hours, fee bps
Job = tuple[str, str, Config, int, float]  # scenario, kind, config, seed, queue fear


def params_of(c: Config) -> Any:
    gate, epoch, vintage, fee = c
    return default_params(gate_bps=gate, epoch_length=epoch * MINUTE, vintage_length=vintage * HOUR, sell_fee_bps=fee)


def run_job(job: Job) -> dict[str, Any]:
    scenario, kind, c, seed, fear = job
    p = params_of(c)
    groups = draw_agents(scenario, seed, DAY // p.epoch_length).group
    m = measures(simulate(scenario, kind, p, seed, fear), groups)
    gate, epoch, vintage, fee = c
    return {
        "scenario": scenario,
        "kind": kind,
        "gate": gate,
        "epoch": epoch,
        "vintage": vintage,
        "fee": fee,
        "seed": seed,
        **m,
    }


def jobs(seeds: int) -> list[Job]:
    out: list[Job] = []
    for seed in range(1, seeds + 1):
        for scenario in SCENARIOS:
            for epoch, vintage, fee in itertools.product(EPOCHS, VINTAGES, FEES):
                out.append((scenario, "fifo", (GATES[0], epoch, vintage, fee), seed, QUEUE_FEAR))  # FIFO: no gate
                for gate in GATES:
                    out.append((scenario, "lifo", (gate, epoch, vintage, fee), seed, QUEUE_FEAR))
    return out


def mean(values: list[float]) -> float:
    vs = [v for v in values if not math.isnan(v)]
    return sum(vs) / len(vs) if vs else float("nan")


def summarise(rows: list[dict[str, Any]]) -> dict[tuple[str, Config], dict[str, float]]:
    """Mean over seeds of each LIFO measure, with the FIFO run on the same seed, epoch, vintage and fee beside it."""
    fifo = {(r["scenario"], r["epoch"], r["vintage"], r["fee"], r["seed"]): r for r in rows if r["kind"] == "fifo"}
    groups: dict[tuple[str, Config], list[dict[str, Any]]] = {}
    for r in rows:
        if r["kind"] == "lifo":
            groups.setdefault((r["scenario"], (r["gate"], r["epoch"], r["vintage"], r["fee"])), []).append(r)
    out = {}
    for key, rs in groups.items():
        base = [fifo[(r["scenario"], r["epoch"], r["vintage"], r["fee"], r["seed"])] for r in rs]
        summary = {}
        for k, v in rs[0].items():
            if isinstance(v, float):
                summary[f"lifo_{k}"] = mean([r[k] for r in rs])
                summary[f"fifo_{k}"] = mean([b[k] for b in base])
        summary["late_gain"] = summary["lifo_late_result"] - summary["fifo_late_result"]
        summary["late_gain_min_seed"] = min(r["late_result"] - b["late_result"] for r, b in zip(rs, base, strict=True))
        out[key] = summary
    return out


def recommend(s: dict[tuple[str, Config], dict[str, float]]) -> tuple[Config | None, list[tuple[Config, float]]]:
    """G1 filter, then rank.

    G1: the late group gets more back under LIFO in the insider dump and in the panic, and calm growth loses at most
    CALM_TOLERANCE. Among the sets that pass, those whose smaller late-group gain is within RANK_BAND of the best are
    treated as equal (the band is below the spread between seeds), and the one with the smallest mean one-hour price
    drop over the two crash scenarios is recommended.
    """
    passing = []
    for c in {c for _, c in s}:
        dump, panic, calm = s[("insider_dump", c)], s[("panic", c)], s[("calm", c)]
        if dump["late_gain"] > 0 and panic["late_gain"] > 0 and calm["late_gain"] >= -CALM_TOLERANCE:
            drop = (dump["lifo_max_1h_drop"] + panic["lifo_max_1h_drop"]) / 2
            passing.append((c, min(dump["late_gain"], panic["late_gain"]), drop))
    if not passing:
        return None, []
    top = max(g for _, g, _ in passing)
    band = [t for t in passing if t[1] >= top - RANK_BAND]
    best = min(band, key=lambda t: (t[2], -t[1]))[0]
    passing.sort(key=lambda t: -t[1])
    return best, [(c, g) for c, g, _ in passing]


def refresh_benefit(s: dict[tuple[str, Config], dict[str, float]], c: Config) -> tuple[float, float]:
    """NEAR the insider gains by refreshing before the dump (LIFO), and what the refresh cost it."""
    gain = (s[("refresh_attack", c)]["lifo_insider_result"] - s[("insider_dump", c)]["lifo_insider_result"]) * 8.0
    return gain, s[("refresh_attack", c)]["lifo_refresh_cost_near"]


COLUMNS = [
    ("late group, NEAR back per NEAR", "late_result"),
    ("early buyers, per NEAR", "early_result"),
    ("insider, per NEAR", "insider_result"),
    ("largest 1 h price drop", "max_1h_drop"),
    ("crash payout to newest third", "crash_share_newest_third"),
    ("crash payout to oldest third", "crash_share_oldest_third"),
    ("oldest holders' longest wait (h)", "oldest_wait_max_h"),
    ("epochs where the gate bound", "gate_bound_epochs"),
]


def fmt(v: float) -> str:
    return "n/a" if math.isnan(v) else f"{v:.3f}" if abs(v) < 100 else f"{v:.0f}"


def header(command: str, seeds: int, commit: str) -> str:
    return (
        f"Simulated, not market data. Command: `{command}`. Seeds 1 to {seeds}, means over seeds. "
        f"Commit `{commit}`. Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots.\n"
    )


def write_tables(s: dict[tuple[str, Config], dict[str, float]], best: Config | None, meta: str) -> None:
    for scenario in SCENARIOS:
        rows = sorted((c, v) for (sc, c), v in s.items() if sc == scenario)
        lines = [
            f"# {scenario}\n",
            meta,
            "\nEach row: one parameter set, LIFO, with FIFO on the same seeds in brackets. "
            "gate bps / epoch min / vintage h / fee bps.\n",
        ]
        lines.append("| params | " + " | ".join(name for name, _ in COLUMNS) + " |")
        lines.append("|---" * (len(COLUMNS) + 1) + "|")
        for c, v in rows:
            mark = " **(launch)**" if c == LAUNCH else " (top of the G1 ranking)" if c == best else ""
            cells = [f"{fmt(v['lifo_' + k])} ({fmt(v['fifo_' + k])})" for _, k in COLUMNS]
            lines.append(f"| {'/'.join(map(str, c))}{mark} | " + " | ".join(cells) + " |")
        (OUT / f"{scenario}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout
        dirty = subprocess.run(["git", "status", "--porcelain", "lifo"], capture_output=True, text=True).stdout
        return sha.strip() + ("-dirty" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep GATE, EPOCH, VINTAGE and FEE over every scenario.")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("at most 4 workers on this machine")
    command = f"python -m lifo.sweep --seeds {args.seeds} --workers {args.workers}"
    commit = git_commit()
    started = time.perf_counter()
    todo = jobs(args.seeds)
    with Pool(args.workers) as pool:
        rows = pool.map(run_job, todo, chunksize=8)
    runtime = time.perf_counter() - started
    OUT.mkdir(exist_ok=True)
    with (OUT / "sweep.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    s = summarise(rows)
    best, passing = recommend(s)
    meta = header(command, args.seeds, commit) + f"Runtime: {runtime:.0f} s for {len(todo)} runs.\n"
    write_tables(s, best, meta)
    from lifo.figures import draw_all

    draw_all(s, LAUNCH, rows, args.seeds)
    write_recommendation(s, best, passing, meta)
    with Pool(args.workers) as pool:
        write_sensitivity(LAUNCH, args.seeds, pool, meta)
    print(f"{len(todo)} runs in {runtime:.0f} s; recommended {best}; {len(passing)} sets pass G1")
    return 0


def write_recommendation(
    s: dict[tuple[str, Config], dict[str, float]], best: Config | None, passing: list[tuple[Config, float]], meta: str
) -> None:
    lines = ["# The grid: gate G1 and the ranking\n", meta]
    total = len({c for _, c in s})
    lines.append(
        f"\nGate G1: {len(passing)} of {total} parameter sets give the late group more NEAR back per NEAR "
        f"under LIFO than under FIFO in both the insider dump and the panic, while calm growth loses at most "
        f"{CALM_TOLERANCE} NEAR per NEAR. Ranking: sets whose smaller late-group gain (insider dump, panic) is "
        f"within {RANK_BAND} of the best count as equal; among them, the smallest mean largest one-hour price drop "
        f"in those two scenarios.\n"
    )
    if best is None:
        lines.append("\nNo parameter set passes G1.\n")
    else:
        gate, epoch, vintage, fee = best
        lines.append(
            f"\nTop of the ranking: GATE_BPS {gate}, EPOCH_LENGTH {epoch} min, VINTAGE_LENGTH {vintage} h, "
            f"SELL_FEE_BPS {fee}. The launch set is {'/'.join(map(str, LAUNCH))} "
            f"({'passes' if LAUNCH in dict(passing) else 'fails'} G1): the 24 h vintage was chosen in run 02 against "
            "the refresh attack, see `results/REFRESH.md` and `results/RECOMMENDED.md`. The table below is the top "
            "of the ranking; the launch set's rows are marked in the scenario tables.\n"
        )
        lines.append(
            "\n| scenario | late group LIFO | late group FIFO | gain | worst seed gain | early LIFO | early FIFO "
            "| insider LIFO | insider FIFO | 1 h drop LIFO | 1 h drop FIFO |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for scenario in SCENARIOS:
            v = s[(scenario, best)]
            cells = [
                v["lifo_late_result"],
                v["fifo_late_result"],
                v["late_gain"],
                v["late_gain_min_seed"],
                v["lifo_early_result"],
                v["fifo_early_result"],
                v["lifo_insider_result"],
                v["fifo_insider_result"],
                v["lifo_max_1h_drop"],
                v["fifo_max_1h_drop"],
            ]
            lines.append(f"| {scenario} | " + " | ".join(fmt(x) for x in cells) + " |")
        gain, cost = refresh_benefit(s, best)
        lines.append(
            f"\nRefresh attack at these parameters: the insider's refresh cost {cost:.2f} NEAR "
            f"and raised what it got out by {gain:.2f} NEAR compared with dumping without a refresh.\n"
        )
        lines.append("\nTop 10 sets passing G1 (gate/epoch/vintage/fee, smaller of the two late-group gains):\n")
        for c, g in passing[:10]:
            lines.append(f"- {'/'.join(map(str, c))}: {g:.3f}")
    (OUT / "SWEEP.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_sensitivity(best: Config, seeds: int, pool: Any, meta: str) -> None:
    """The queue-fear assumption (holders panic sooner when the last batch hit the gate), varied at the best set."""
    fears = (0.5, QUEUE_FEAR, 1.0)
    todo = [
        (sc, "lifo", best, seed, f)
        for f in fears
        for sc in ("calm", "insider_dump", "panic")
        for seed in range(1, seeds + 1)
    ]
    rows = pool.map(run_job, todo)
    lines = [
        "# Sensitivity to the queue-fear assumption\n",
        meta,
        f"\nLIFO at {'/'.join(map(str, best))}. A holder normally sells everything when the price falls a drawn "
        "fraction below its peak since entry; after a batch where the gate bound, that fraction is multiplied by the "
        "queue fear (1.0 = the visible queue changes nothing, 0.5 = holders panic at half the usual fall). "
        "Late group, NEAR back per NEAR, mean over seeds:\n",
        "| queue fear | calm | insider dump | panic |",
        "|---|---|---|---|",
    ]
    for f in fears:
        cells = [
            mean([r["late_result"] for r, j in zip(rows, todo, strict=True) if j[4] == f and j[0] == sc])
            for sc in ("calm", "insider_dump", "panic")
        ]
        lines.append(f"| {f} | " + " | ".join(fmt(x) for x in cells) + " |")
    (OUT / "SENSITIVITY.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
