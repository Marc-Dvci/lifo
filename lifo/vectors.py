"""Test vectors for the contract (M3): operation sequences and the model's state after every step.

    python -m lifo.vectors            writes vectors/edge.json and vectors/random.json
    python -m lifo.vectors --check    exits 1 if the committed files differ from what the model produces now

The output is deterministic (fixed seed, no timestamps), so the files only change when the model changes.
Amounts are decimal strings: they exceed both 2^53 and, for k, 2^128.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lifo.model import DAY, MINUTE, NEAR, TOKEN, Lifo, LifoError, Params, gate_fill

SEED = 20261107
RANDOM_CASES = 200
OUT = Path(__file__).resolve().parent.parent / "vectors"
COMMAND = "python -m lifo.vectors"

Step = dict[str, Any]


def small(**overrides: int) -> Params:
    values = dict(
        virtual_near=10_000,
        virtual_tokens=1_000_000,
        max_supply=800_000,
        gate_bps=1000,
        sell_fee_bps=100,
        epoch_length=10,
        vintage_length=20,
        launch_time=100,
        max_lots=3,
    )
    values.update(overrides)
    return Params(**values)


def realistic(**overrides: int) -> Params:
    values = dict(
        virtual_near=30 * NEAR,
        virtual_tokens=1_073_000_000 * TOKEN,
        max_supply=1_073_000_000 * TOKEN * 8 // 10,
        gate_bps=500,
        sell_fee_bps=100,
        epoch_length=10 * MINUTE,
        vintage_length=DAY,
        launch_time=1_800_000_000 * 10**9,
        max_lots=16,
    )
    values.update(overrides)
    return Params(**values)


def encode_result(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return [encode_result(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return encode_result(list(asdict(value).values()))
    raise TypeError(type(value))


class Recorder:
    """Applies operations to a model and records each one with its result and the state after it."""

    def __init__(self, name: str, params: Params) -> None:
        self.name = name
        self.m = Lifo(params)
        self.steps: list[Step] = []

    def do(self, op: str, **args: Any) -> Any:
        call: Callable[..., Any] = getattr(self.m, op)
        step: Step = {"op": op, **{k: (str(v) if isinstance(v, int) and k != "now" else v) for k, v in args.items()}}
        try:
            result = call(**args)
            step["result"] = encode_result(result)
        except LifoError as e:
            result = None
            step["error"] = str(e)
        state = self.m.state()  # settlements are checked once, in the final state, to keep the files small
        step["state"] = {k: v for k, v in state.items() if k not in ("settlements", "pending")}
        self.steps.append(step)
        return result

    def case(self) -> dict[str, Any]:
        params = {k: str(v) for k, v in asdict(self.m.params).items()}
        return {"name": self.name, "params": params, "steps": self.steps, "final": self.m.state()}


def t(p: Params, epoch: int, offset: int = 0) -> int:
    return p.launch_time + epoch * p.epoch_length + offset


# --- Edge cases ----------------------------------------------------------------------------------------------------


def edge_gate_exactly(extra: int) -> dict[str, Any]:
    """Requests of exactly F (the gate does not bind) or F + 1 (it binds by one unit)."""
    p = small()
    r = Recorder(f"gate exactly at F plus {extra}", p)
    r.do("buy", account="a", deposit=10_000, now=t(p, 0))
    r.do("buy", account="b", deposit=2_000, now=t(p, 2))
    cap = r.m.reserve * p.gate_bps // 10_000
    f = gate_fill(r.m.x, r.m.y, cap, p.sell_fee_bps)
    assert f is not None
    r.do("request_sell", account="b", amount=100, now=t(p, 3))
    r.do("request_sell", account="a", amount=f + extra - 100, now=t(p, 3))
    r.do("settle", now=t(p, 4))
    r.do("claim", account="a", now=t(p, 4))
    r.do("claim", account="b", now=t(p, 4))
    return r.case()


def edge_boundary_pro_rata() -> dict[str, Any]:
    """Three requests share the boundary vintage; odd amounts leave token and NEAR rounding to settle."""
    p = small(gate_bps=200, max_lots=4)
    r = Recorder("boundary vintage pro rata with dust", p)
    r.do("buy", account="a", deposit=7_001, now=t(p, 0))
    r.do("transfer", sender="a", receiver="b", amount=100_003, now=t(p, 0))
    r.do("transfer", sender="a", receiver="c", amount=33_337, now=t(p, 0))
    r.do("buy", account="d", deposit=1_003, now=t(p, 2))
    for who in "abcd":
        r.do("request_sell", account=who, amount=r.m.balance(who), now=t(p, 3))
    r.do("settle", now=t(p, 4))
    for who in "dcba":
        r.do("claim", account=who, now=t(p, 4, 1))
    return r.case()


def edge_lot_merges() -> dict[str, Any]:
    """An account at MAX_LOTS buys and receives more vintages; the two oldest merge each time."""
    p = small(max_lots=3)
    r = Recorder("lot merges at MAX_LOTS", p)
    for i in range(5):
        r.do("buy", account="a", deposit=500, now=t(p, 2 * i))
    for i in range(5):
        r.do("buy", account="b", deposit=300 + i, now=t(p, 2 * i + 10))
    r.do("transfer", sender="b", receiver="a", amount=r.m.balance("b") // 2, now=t(p, 20))
    r.do("transfer", sender="a", receiver="c", amount=r.m.balance("a"), now=t(p, 20))
    r.do("request_sell", account="c", amount=r.m.balance("c") // 3, now=t(p, 21))
    r.do("buy", account="c", deposit=50, now=t(p, 23))
    return r.case()


def edge_transfer_call() -> dict[str, Any]:
    """ft_transfer_call refunds: partial, more than sent, more than the receiver still holds, none."""
    p = small(max_lots=4)
    r = Recorder("ft_transfer_call refund vintages", p)
    for i, who in enumerate("aab"):
        r.do("buy", account=who, deposit=1_000 + i, now=t(p, 2 * i))
    r.do("transfer_call", sender="a", receiver="dex", amount=r.m.balance("a") - 7, unused=1_234, now=t(p, 4))
    r.do("transfer_call", sender="b", receiver="dex", amount=500, unused=10**6, now=t(p, 4))
    r.do("transfer", sender="dex", receiver="c", amount=r.m.balance("dex") - 3, now=t(p, 5))
    r.do("transfer_call", sender="c", receiver="dex", amount=10, unused=99, now=t(p, 6))
    r.do("transfer_call", sender="a", receiver="dex", amount=7, unused=0, now=t(p, 6))
    return r.case()


def edge_errors() -> dict[str, Any]:
    """Every refusal, each leaving the state unchanged."""
    p = small(virtual_near=10**6, virtual_tokens=10**5, max_supply=8 * 10**4)
    r = Recorder("refusals", p)
    r.do("buy", account="a", deposit=10, now=p.launch_time - 1)
    r.do("buy", account="a", deposit=0, now=t(p, 1))
    r.do("buy", account="a", deposit=1, now=t(p, 1))
    r.do("buy", account="a", deposit=10_000, now=t(p, 1))
    r.do("buy", account="b", deposit=10**7, now=t(p, 1))
    r.do("request_sell", account="a", amount=0, now=t(p, 1))
    r.do("request_sell", account="a", amount=10**9, now=t(p, 1))
    r.do("request_sell", account="a", amount=500, now=t(p, 1))
    r.do("transfer", sender="a", receiver="a", amount=5, now=t(p, 1))
    r.do("transfer", sender="a", receiver="b", amount=10**9, now=t(p, 1))
    r.do("claim", account="a", now=t(p, 1))
    r.do("buy", account="a", deposit=0, now=t(p, 2))  # refused: the settlement it triggered is undone too
    r.do("buy", account="a", deposit=10, now=t(p, 0))
    r.do("claim", account="a", now=t(p, 2))
    r.do("claim", account="a", now=t(p, 2))
    return r.case()


def edge_rounding_dust() -> dict[str, Any]:
    """A tiny pool where every division leaves a remainder; dust NEAR returns to the reserve at the last claim."""
    p = small(virtual_near=97, virtual_tokens=10_007, max_supply=9_001, gate_bps=777, sell_fee_bps=333, max_lots=2)
    r = Recorder("rounding dust in a tiny pool", p)
    for i, who in enumerate("abcab"):
        r.do("buy", account=who, deposit=13 + i, now=t(p, i))
    for who in "abc":
        r.do("request_sell", account=who, amount=r.m.balance(who) * 2 // 3 + 1, now=t(p, 6))
    r.do("settle", now=t(p, 7))
    for who in "cab":
        r.do("claim", account=who, now=t(p, 7))
    for who in "abc":
        r.do("request_sell", account=who, amount=r.m.balance(who), now=t(p, 8))
    r.do("buy", account="d", deposit=29, now=t(p, 8, 5))
    for who in "abcd":
        r.do("claim", account=who, now=t(p, 12))
    return r.case()


def edge_crash_realistic() -> dict[str, Any]:
    """The starting curve in yoctoNEAR: an early holder and a late buyer sell into a binding gate for two epochs."""
    p = realistic()
    r = Recorder("crash on the starting curve", p)
    r.do("buy", account="early", deposit=20 * NEAR, now=t(p, 0))
    for i in range(6):
        r.do("buy", account=f"late{i}", deposit=(3 + i) * NEAR, now=t(p, 6 * 24 + i))
    sellers = ["early"] + [f"late{i}" for i in range(6)]
    for who in sellers:
        r.do("request_sell", account=who, amount=r.m.balance(who), now=t(p, 300))
    for who in sellers:  # the first call of epoch 301 settles epoch 300; each claim gives back the unfilled tokens
        r.do("claim", account=who, now=t(p, 301))
        if r.m.balance(who):
            r.do("request_sell", account=who, amount=r.m.balance(who), now=t(p, 301))
    r.do("settle", now=t(p, 302))
    for who in ["early"] + [f"late{i}" for i in range(6)]:
        r.do("claim", account=who, now=t(p, 302, 1))
    r.do("settle", now=t(p, 302) + DAY)
    return r.case()


def edge_cases() -> list[dict[str, Any]]:
    return [
        edge_gate_exactly(0),
        edge_gate_exactly(1),
        edge_boundary_pro_rata(),
        edge_lot_merges(),
        edge_transfer_call(),
        edge_errors(),
        edge_rounding_dust(),
        edge_crash_realistic(),
    ]


# --- Random sequences ----------------------------------------------------------------------------------------------


def random_case(rng: random.Random, index: int) -> dict[str, Any]:
    if rng.random() < 0.25:
        p = realistic(gate_bps=rng.choice([200, 500, 1000, 2000]), sell_fee_bps=rng.choice([50, 100, 200, 300]))
    else:
        vt = rng.choice([10_007, 1_000_000, 10**9])
        p = small(
            virtual_near=rng.choice([97, 10_000, 10**8]),
            virtual_tokens=vt,
            max_supply=vt * rng.choice([5, 8, 9]) // 10,
            gate_bps=rng.choice([1, 200, 500, 1000, 5000]),
            sell_fee_bps=rng.choice([0, 1, 100, 300]),
            vintage_length=10 * rng.choice([1, 2, 3]),
            max_lots=rng.choice([1, 2, 3, 16]),
        )
    r = Recorder(f"random {index}", p)
    accounts = ["a", "b", "c"]
    now = p.launch_time
    for _ in range(rng.randint(12, 24)):
        now += rng.choice([0, 0, 1, p.epoch_length // 2, p.epoch_length, 2 * p.epoch_length, p.vintage_length])
        who, other = rng.sample(accounts, 2)
        bal = r.m.balance(who)
        roll = rng.random()
        if roll < 0.35 or bal == 0:
            r.do("buy", account=who, deposit=max(1, p.virtual_near * rng.randint(1, 400) // 1000), now=now)
        elif roll < 0.6:
            r.do("request_sell", account=who, amount=rng.randint(1, bal), now=now)
        elif roll < 0.72:
            r.do("transfer", sender=who, receiver=other, amount=rng.randint(1, bal), now=now)
        elif roll < 0.8:
            amount = rng.randint(1, bal)
            r.do("transfer_call", sender=who, receiver=other, amount=amount, unused=rng.randint(0, amount), now=now)
        elif roll < 0.92:
            r.do("claim", account=who, now=now)
        else:
            r.do("settle", now=now)
    return r.case()


def random_cases() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    return [random_case(rng, i) for i in range(RANDOM_CASES)]


def render(kind: str, cases: list[dict[str, Any]]) -> str:
    """One case per line, so a change in the model shows as a readable diff."""
    header = json.dumps({"command": COMMAND, "seed": SEED, "kind": kind, "count": len(cases)})
    body = ",\n".join(json.dumps(c, separators=(",", ":")) for c in cases)
    return f'{{"about":{header},\n"cases":[\n{body}\n]}}\n'


def files() -> dict[Path, str]:
    return {OUT / "edge.json": render("edge", edge_cases()), OUT / "random.json": render("random", random_cases())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="compare with the committed files instead of writing")
    args = parser.parse_args(argv)
    stale = []
    for path, text in files().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"{path.name}: {text.count(chr(10)) - 3} cases, {len(text) // 1024} KiB")
    if stale:
        print("stale vectors: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
