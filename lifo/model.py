"""The LIFO reference model: the pool, vintages and the gate (requirements A, B and C).

Python integers only. Every rounding favours the reserve. The contract is written from this file and replays the
vectors it produces (`python -m lifo.vectors`).

Amounts: NEAR in yoctoNEAR, tokens in the smallest unit. Time: nanoseconds since the Unix epoch.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar, cast

BPS = 10_000

# Stable error messages: the tests and the contract assert on these exact strings.
E_PARAMS = "invalid parameters"
E_NOT_LAUNCHED = "not launched"
E_TIME = "time went backwards"
E_ZERO = "zero amount"
E_BUY_TOO_SMALL = "buy too small"
E_MAX_SUPPLY = "max supply reached"
E_BALANCE = "not enough tokens"
E_SELF = "sender is receiver"
E_NOTHING = "nothing to claim"


class LifoError(Exception):
    """A refused call. Its message is one of the E_* constants."""


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


# --- The curve (A2, A4) and the gate (C3): pure functions of the pool ---------------------------------------------


def buy_out(x: int, y: int, deposit: int) -> int:
    """Tokens a buy of `deposit` yoctoNEAR receives (A2), rounded down for the buyer."""
    return y - ceil_div(x * y, x + deposit)


def sell_gross(x: int, y: int, tokens: int) -> int:
    """NEAR the curve gives for `tokens` before the fee (A4), rounded down for the seller."""
    return x - ceil_div(x * y, y + tokens)


def after_fee(gross: int, fee_bps: int) -> int:
    """What a seller gets from `gross`: the fee is rounded up and stays in the reserve (A4)."""
    return gross - ceil_div(gross * fee_bps, BPS)


def sell_paid(x: int, y: int, tokens: int, fee_bps: int) -> int:
    return after_fee(sell_gross(x, y, tokens), fee_bps)


def gate_fill(x: int, y: int, cap: int, fee_bps: int) -> int | None:
    """The largest token amount F with sell_paid(F) <= cap (C3), or None if every amount fits.

    Proof sketch (design.md): paid as a function of gross g is floor(g (B - fee) / B), so the largest allowed gross is
    G = floor(((cap + 1) B - 1) / (B - fee)). gross(f) <= G exactly when ceil(k / (Y + f)) >= m with m = X - G, which
    for m >= 2 is (m - 1)(Y + f) < k, that is Y + f <= floor((k - 1) / (m - 1)).
    """
    largest_gross = ((cap + 1) * BPS - 1) // (BPS - fee_bps)
    m = x - largest_gross
    if m <= 1:
        return None
    return (x * y - 1) // (m - 1) - y


# --- Parameters (A1) ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    """Every parameter, fixed at initialisation. Nothing can change them afterwards (A1)."""

    virtual_near: int
    virtual_tokens: int
    max_supply: int
    gate_bps: int
    sell_fee_bps: int
    epoch_length: int
    vintage_length: int
    launch_time: int
    max_lots: int

    def __post_init__(self) -> None:
        ok = (
            self.virtual_near > 0
            and 0 < self.max_supply < self.virtual_tokens
            and 0 < self.gate_bps <= BPS
            and 0 <= self.sell_fee_bps < BPS
            and self.epoch_length > 0
            and self.vintage_length > 0
            and self.vintage_length % self.epoch_length == 0
            and self.launch_time >= 0
            and self.max_lots >= 1
        )
        if not ok:
            raise LifoError(E_PARAMS)


NEAR = 10**24
TOKEN = 10**18
MINUTE = 60 * 10**9
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def default_params(**overrides: int) -> Params:
    """The starting curve of S5 with the recommended gate, epoch, vintage and fee (results/RECOMMENDED.md)."""
    values: dict[str, int] = {
        "virtual_near": 30 * NEAR,
        "virtual_tokens": 1_073_000_000 * TOKEN,
        "max_supply": 1_073_000_000 * TOKEN * 8 // 10,
        "gate_bps": 500,
        "sell_fee_bps": 50,
        "epoch_length": 10 * MINUTE,
        "vintage_length": HOUR,
        "launch_time": 0,
        "max_lots": 16,
    }
    values.update(overrides)
    return Params(**values)


# --- State ---------------------------------------------------------------------------------------------------------

Lots = list[list[int]]  # [[vintage, amount], ...], ascending by vintage, distinct vintages, amounts > 0


@dataclass
class Request:
    id: int
    owner: str
    epoch: int
    portions: list[tuple[int, int, int]]  # (vintage, amount, start): start is the vintage's total before this portion
    claimed: bool = False

    @property
    def amount(self) -> int:
        return sum(a for _, a, _ in self.portions)


@dataclass
class Settlement:
    epoch: int
    cap: int
    max_fill: int | None  # F; None when the gate cannot bind
    requested: dict[int, int]
    filled: dict[int, int]
    total_requested: int
    total_filled: int
    paid: int
    bound: bool
    open_requests: int
    paid_left: int
    dust_returned: int = 0


@dataclass(frozen=True)
class Claim:
    """The result of one request (C6, C7, C9)."""

    request: int
    filled: int
    paid: int
    returned: tuple[tuple[int, int], ...]  # (vintage, amount) given back to the owner


Fn = TypeVar("Fn", bound=Callable[..., Any])


def atomic(method: Fn) -> Fn:
    """A refused call leaves no trace, not even the settlement and claims it ran first (a contract panic reverts all)."""

    @wraps(method)
    def wrapper(self: Lifo, *args: Any, **kwargs: Any) -> Any:
        if not self.transactional:
            return method(self, *args, **kwargs)
        saved = copy.deepcopy(self.__dict__)
        try:
            return method(self, *args, **kwargs)
        except LifoError:
            self.__dict__ = saved
            raise

    return cast(Fn, wrapper)


@dataclass
class Lifo:
    params: Params
    transactional: bool = True
    now: int = 0
    reserve: int = 0
    supply: int = 0
    lots: dict[str, Lots] = field(default_factory=dict)
    requests: dict[int, Request] = field(default_factory=dict)
    unclaimed_ids: dict[str, list[int]] = field(default_factory=dict)
    open_epoch: int | None = None
    open_requested: dict[int, int] = field(default_factory=dict)
    open_count: int = 0
    settlements: dict[int, Settlement] = field(default_factory=dict)
    unclaimed_near: int = 0
    deposited: int = 0
    paid_out: dict[str, int] = field(default_factory=dict)
    next_id: int = 0

    def __post_init__(self) -> None:
        self.now = self.params.launch_time

    # --- views (D1) ---

    @property
    def x(self) -> int:
        return self.params.virtual_near + self.reserve

    @property
    def y(self) -> int:
        return self.params.virtual_tokens - self.supply

    @property
    def k(self) -> int:
        return self.x * self.y

    def epoch(self, t: int) -> int:
        return (t - self.params.launch_time) // self.params.epoch_length

    def vintage(self, t: int) -> int:
        return (t - self.params.launch_time) // self.params.vintage_length

    def balance(self, account: str) -> int:
        return sum(a for _, a in self.lots.get(account, []))

    def quote_buy(self, deposit: int) -> int:
        return buy_out(self.x, self.y, deposit)

    def result(self, request_id: int) -> Claim:
        """What a settled request is owed, from the epoch's per-vintage aggregates (C8)."""
        r = self.requests[request_id]
        s = self.settlements[r.epoch]
        filled = 0
        returned = []
        for v, a, start in r.portions:
            f_v, r_v = s.filled[v], s.requested[v]
            got = (start + a) * f_v // r_v - start * f_v // r_v
            filled += got
            if a > got:
                returned.append((v, a - got))
        paid = filled * s.paid // s.total_filled if s.total_filled else 0
        return Claim(r.id, filled, paid, tuple(returned))

    def state(self) -> dict[str, Any]:
        """Everything the contract must agree on, as JSON (amounts as decimal strings: they exceed 2^53)."""
        return {
            "reserve": str(self.reserve),
            "supply": str(self.supply),
            "unclaimed_near": str(self.unclaimed_near),
            "lots": {a: [[v, str(n)] for v, n in lots] for a, lots in sorted(self.lots.items()) if lots},
            "paid_out": {a: str(n) for a, n in sorted(self.paid_out.items()) if n},
            "open_epoch": self.open_epoch,
            "open_requested": {str(v): str(n) for v, n in sorted(self.open_requested.items())},
            "pending": {a: ids for a, ids in sorted(self.unclaimed_ids.items()) if ids},
            "settlements": {
                str(e): {
                    "cap": str(s.cap),
                    "max_fill": None if s.max_fill is None else str(s.max_fill),
                    "filled": {str(v): str(n) for v, n in sorted(s.filled.items())},
                    "total_filled": str(s.total_filled),
                    "paid": str(s.paid),
                    "bound": s.bound,
                    "open_requests": s.open_requests,
                    "paid_left": str(s.paid_left),
                }
                for e, s in sorted(self.settlements.items())
            },
        }

    # --- calls ---

    @atomic
    def buy(self, account: str, deposit: int, now: int) -> int:
        """A2, A3. Returns the tokens bought."""
        self._begin(now, account)
        if deposit <= 0:
            raise LifoError(E_ZERO)
        out = self.quote_buy(deposit)
        if out <= 0:
            raise LifoError(E_BUY_TOO_SMALL)
        if self.supply + out > self.params.max_supply:
            raise LifoError(E_MAX_SUPPLY)
        self.reserve += deposit
        self.supply += out
        self.deposited += deposit
        self._add(account, self.vintage(now), out)
        return out

    @atomic
    def request_sell(self, account: str, amount: int, now: int) -> int:
        """C1. Locks `amount` tokens, newest first, into a request for the current epoch. Returns the request id."""
        self._begin(now, account)
        if amount <= 0:
            raise LifoError(E_ZERO)
        e = self.epoch(now)
        portions = []
        for v, a in self._take(account, amount):
            start = self.open_requested.get(v, 0)
            self.open_requested[v] = start + a
            portions.append((v, a, start))
        self.open_epoch = e
        self.open_count += 1
        rid = self.next_id
        self.next_id += 1
        self.requests[rid] = Request(rid, account, e, portions)
        self.unclaimed_ids.setdefault(account, []).append(rid)
        return rid

    @atomic
    def transfer(self, sender: str, receiver: str, amount: int, now: int) -> list[tuple[int, int]]:
        """B1, B2. Returns the (vintage, amount) lots that moved."""
        self._begin(now, sender)
        return self._transfer(sender, receiver, amount)

    @atomic
    def transfer_call(self, sender: str, receiver: str, amount: int, unused: int, now: int) -> int:
        """B4. A transfer whose receiver hands back `unused` tokens. Returns the amount refunded to the sender."""
        self._begin(now, sender)
        moved = self._transfer(sender, receiver, amount)
        refund = min(max(unused, 0), amount, self.balance(receiver))
        if refund:
            self._take(receiver, refund)
            self._add(sender, min(v for v, _ in moved), refund)
        return refund

    @atomic
    def claim(self, account: str, now: int) -> list[Claim]:
        """C9. Pays every settled request of `account` once."""
        self._begin(now, None)
        claims = self._claim_settled(account)
        if not claims:
            raise LifoError(E_NOTHING)
        return claims

    @atomic
    def settle(self, now: int) -> int:
        """C2. Anyone may call it. Returns how many epochs it settled (0 or 1)."""
        before = self.open_epoch
        self._begin(now, None)
        return int(before is not None and self.open_epoch is None)

    # --- internals ---

    def _begin(self, now: int, actor: str | None) -> None:
        if now < self.params.launch_time:
            raise LifoError(E_NOT_LAUNCHED)
        if now < self.now:
            raise LifoError(E_TIME)
        self.now = now
        self._settle_finished()
        if actor is not None:
            self._claim_settled(actor)

    def _settle_finished(self) -> None:
        """C2 to C5. Settles the one epoch with requests once it is over, on the pool as it is now."""
        e = self.open_epoch
        if e is None or self.epoch(self.now) <= e:
            return
        fee = self.params.sell_fee_bps
        cap = self.reserve * self.params.gate_bps // BPS
        max_fill = gate_fill(self.x, self.y, cap, fee)
        requested = dict(self.open_requested)
        total = sum(requested.values())
        bound = max_fill is not None and total > max_fill
        left = min(total, max_fill) if max_fill is not None else total
        total_filled = left
        filled = {}
        for v in sorted(requested, reverse=True):
            filled[v] = min(requested[v], left)
            left -= filled[v]
        paid = sell_paid(self.x, self.y, total_filled, fee)
        self.reserve -= paid
        self.supply -= total_filled
        self.unclaimed_near += paid
        self.settlements[e] = Settlement(
            e, cap, max_fill, requested, filled, total, total_filled, paid, bound, self.open_count, paid
        )
        self.open_epoch = None
        self.open_requested = {}
        self.open_count = 0

    def _claim_settled(self, account: str) -> list[Claim]:
        claims = []
        keep = []
        for rid in self.unclaimed_ids.get(account, []):
            r = self.requests[rid]
            if r.epoch not in self.settlements:
                keep.append(rid)
                continue
            c = self.result(rid)
            r.claimed = True
            s = self.settlements[r.epoch]
            for v, a in c.returned:
                self._add(account, v, a)
            s.paid_left -= c.paid
            s.open_requests -= 1
            self.unclaimed_near -= c.paid
            self.paid_out[account] = self.paid_out.get(account, 0) + c.paid
            if s.open_requests == 0 and s.paid_left:
                s.dust_returned = s.paid_left
                self.reserve += s.paid_left
                self.unclaimed_near -= s.paid_left
                s.paid_left = 0
            claims.append(c)
        if account in self.unclaimed_ids:
            self.unclaimed_ids[account] = keep
        return claims

    def _transfer(self, sender: str, receiver: str, amount: int) -> list[tuple[int, int]]:
        if sender == receiver:
            raise LifoError(E_SELF)
        if amount <= 0:
            raise LifoError(E_ZERO)
        moved = self._take(sender, amount)
        for v, a in moved:
            self._add(receiver, v, a)
        return moved

    def _take(self, account: str, amount: int) -> list[tuple[int, int]]:
        """B1. Removes `amount` from the newest lots first; returns the (vintage, amount) taken, newest first."""
        lots = self.lots.get(account, [])
        if sum(a for _, a in lots) < amount:
            raise LifoError(E_BALANCE)
        taken = []
        while amount:
            v, a = lots[-1]
            got = min(a, amount)
            taken.append((v, got))
            amount -= got
            if got == a:
                lots.pop()
            else:
                lots[-1][1] = a - got
        return taken

    def _add(self, account: str, vintage: int, amount: int) -> None:
        """B2, B3. Adds a lot, merged with one of equal vintage; merges the two oldest while there are too many."""
        lots = self.lots.setdefault(account, [])
        i = len(lots)
        while i and lots[i - 1][0] > vintage:
            i -= 1
        if i and lots[i - 1][0] == vintage:
            lots[i - 1][1] += amount
        else:
            lots.insert(i, [vintage, amount])
        while len(lots) > self.params.max_lots:
            lots[0][1] += lots.pop(1)[1]
