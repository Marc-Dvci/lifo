"""The vectors (M3) are what the model produces now, and a replay that reads only the JSON reproduces every step."""

from __future__ import annotations

import json
from typing import Any

from lifo import vectors
from lifo.model import Lifo, LifoError, Params

CASES = [
    case
    for name in ("edge.json", "random.json")
    for case in json.loads((vectors.OUT / name).read_text(encoding="utf-8"))["cases"]
]


def test_the_committed_vectors_are_exactly_what_the_model_produces() -> None:
    assert vectors.main(["--check"]) == 0


def test_the_edge_vectors_cover_the_cases_the_contract_must_get_right() -> None:
    finals = {c["name"]: c["final"]["settlements"] for c in CASES}
    assert not finals["gate exactly at F plus 0"]["3"]["bound"]
    assert finals["gate exactly at F plus 1"]["3"]["bound"]
    errors = {s["error"] for c in CASES for s in c["steps"] if "error" in s}
    assert errors == {
        "not launched",
        "time went backwards",
        "zero amount",
        "buy too small",
        "max supply reached",
        "not enough tokens",
        "sender is receiver",
        "nothing to claim",
    }


def replay_args(step: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in step.items():
        if key in ("op", "result", "error", "state"):
            continue
        out[key] = int(value) if key in ("deposit", "amount", "unused", "now") else value
    return out


def test_a_replay_from_the_json_alone_agrees_at_every_step() -> None:
    for case in CASES:
        replay(case)


def replay(case: dict[str, Any]) -> None:
    m = Lifo(Params(**{k: int(v) for k, v in case["params"].items()}))
    for step in case["steps"]:
        try:
            result = vectors.encode_result(getattr(m, step["op"])(**replay_args(step)))
            assert step.get("result") == result
        except LifoError as e:
            assert step.get("error") == str(e)
        state = m.state()
        assert {k: v for k, v in state.items() if k in step["state"]} == step["state"]
    assert m.state() == case["final"]
