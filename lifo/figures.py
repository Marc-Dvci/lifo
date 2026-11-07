"""Figures for results/figures/: white background, no title inside the image (the caption goes in the README)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lifo.sim import SCENARIOS, simulate  # noqa: E402

FIGS = Path(__file__).resolve().parent.parent / "results" / "figures"
FIFO_STYLE = {"color": "#9a9a9a", "lw": 1.2, "label": "FIFO baseline"}
LIFO_STYLE = {"color": "#c0392b", "lw": 1.2, "label": "LIFO"}

plt.rcParams.update(
    {
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


def days(run: Any) -> list[float]:
    step_days = run.params.epoch_length / (24 * 3600 * 10**9)
    return [i * step_days for i in range(len(run.price))]


def save(fig: Any, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / name, dpi=200, bbox_inches="tight")
    plt.close(fig)


def paths(best: Any, seed: int = 1) -> None:
    from lifo.sweep import params_of

    p = params_of(best)
    fifo, lifo = simulate("insider_dump", "fifo", p, seed), simulate("insider_dump", "lifo", p, seed)
    fig, axes = plt.subplots(3, 1, figsize=(8.5, 6.5), sharex=True)
    for run, style in ((fifo, FIFO_STYLE), (lifo, LIFO_STYLE)):
        axes[0].plot(days(run), [x * 1e9 for x in run.price], **style)
        axes[1].plot(days(run), run.reserve, **style)
        axes[2].plot(days(run), [x / 1e6 for x in run.supply], **style)
    axes[0].set_ylabel("price\n(NEAR per 1e9 tokens)")
    axes[1].set_ylabel("reserve (NEAR)")
    axes[2].set_ylabel("supply\n(million tokens)")
    axes[2].set_xlabel("day (the 6 days around the dump; the 60-day paths are in scenarios_price.png)")
    axes[2].set_xlim(17, 23)
    axes[0].legend(frameon=False, loc="upper left")
    save(fig, "insider_dump_paths.png")


def scenarios(best: Any, seed: int = 1) -> None:
    from lifo.sweep import params_of

    p = params_of(best)
    fig, axes = plt.subplots(len(SCENARIOS), 1, figsize=(8.5, 8.5), sharex=True)
    for ax, scenario in zip(axes, SCENARIOS, strict=True):
        for kind, style in (("fifo", FIFO_STYLE), ("lifo", LIFO_STYLE)):
            run = simulate(scenario, kind, p, seed)
            ax.plot(days(run), [x * 1e9 for x in run.price], **style)
        ax.set_ylabel(scenario.replace("_", " "), rotation=0, ha="right", va="center")
        ax.set_yticks([])
    axes[0].legend(frameon=False, loc="upper left", ncol=2)
    axes[-1].set_xlabel("day (price, same scale within each row)")
    save(fig, "scenarios_price.png")


def payout_by_vintage(s: dict[Any, dict[str, float]], best: Any) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    labels, positions = [], []
    width = 0.38
    for j, scenario in enumerate(("insider_dump", "panic")):
        v = s[(scenario, best)]
        for k, kind in enumerate(("fifo", "lifo")):
            newest, oldest = v[f"{kind}_crash_share_newest_third"], v[f"{kind}_crash_share_oldest_third"]
            shares = [oldest, 1 - newest - oldest, newest]
            x = [j * 4 + i + (k - 0.5) * width for i in range(3)]
            style = FIFO_STYLE if kind == "fifo" else LIFO_STYLE
            ax.bar(x, shares, width, color=style["color"], label=style["label"] if j == 0 else None)
        labels += [f"{scenario.replace('_', ' ')}\n{t}" for t in ("oldest third", "middle third", "newest third")]
        positions += [j * 4 + i for i in range(3)]
    ax.set_xticks(positions, labels, fontsize=8)
    ax.set_ylabel("share of NEAR paid out\nin the 48 h after the peak")
    ax.legend(frameon=False)
    save(fig, "crash_payout_by_vintage.png")


def refresh(s: dict[Any, dict[str, float]], best: Any) -> None:
    from lifo.sweep import FEES, refresh_benefit

    gate, epoch, vintage, _ = best
    costs, gains = [], []
    for fee in FEES:
        gain, cost = refresh_benefit(s, (gate, epoch, vintage, fee))
        costs.append(cost)
        gains.append(gain)
    fig, ax = plt.subplots(figsize=(8.5, 3.2))
    x = range(len(FEES))
    ax.bar([i - 0.2 for i in x], costs, 0.4, color="#9a9a9a", label="what the refresh cost the insider (NEAR)")
    ax.bar([i + 0.2 for i in x], gains, 0.4, color="#c0392b", label="what it added to the insider's exit (NEAR)")
    ax.set_xticks(list(x), [f"sell fee {f} bps" for f in FEES])
    ax.axhline(0, color="black", lw=0.6)
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8)
    save(fig, "refresh_cost_benefit.png")


def draw_all(s: dict[Any, dict[str, float]], best: Any, rows: list[dict[str, Any]], seeds: int) -> None:
    if best is None:
        return
    paths(best)
    scenarios(best)
    payout_by_vintage(s, best)
    refresh(s, best)
