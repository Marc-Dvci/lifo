# LIFO: requirements

The mechanism as numbered requirements. The contract's tests are named after them (`a1_…`, `b4_…`, `c5_…` in
[`contract/tests/requirements.rs`](../contract/tests/requirements.rs)), the reference model implements groups A to C
([`lifo/model.py`](../lifo/model.py)), and the simulation answers group S. Form: "WHEN …, THE system SHALL …".
Parameters in `CAPITALS` are fixed at launch and readable by anyone. Where a requirement was changed, the old one is
kept with the reason and the evidence. The rules in plain words: [`RULES.md`](../RULES.md).

## Glossary

- **Pool**: the coin's own market, a constant-product curve with virtual reserves. `X = VIRTUAL_NEAR + reserve`, `Y = VIRTUAL_TOKENS - supply`, `k = X * Y`.
- **reserve**: real NEAR (yoctoNEAR) held for the pool. **supply**: tokens sold by the pool and not yet bought back.
- **Epoch** (a round): `epoch(t) = floor((t - LAUNCH_TIME) / EPOCH_LENGTH)`, from the block timestamp.
- **Vintage** (a plate): `vintage(t) = floor((t - LAUNCH_TIME) / VINTAGE_LENGTH)`, `VINTAGE_LENGTH` a whole multiple of `EPOCH_LENGTH`. The vintage of a token is the vintage in which it was bought from the pool. A higher number is newer.
- **Lot**: an amount of tokens of one vintage held by one account. An account holds at most `MAX_LOTS` lots.

## A. The pool

A1. THE system SHALL have no owner, no admin method, no upgrade method and no method that changes a parameter after initialisation. Initialisation takes every parameter and can run once.

A2. WHEN an account buys with `d` yoctoNEAR (after any storage cost), THE system SHALL give it `out = Y - ceil(k / (X + d))` tokens (rounded down for the buyer), add `d` to the reserve, add `out` to supply, and record `out` as a lot of the current vintage. Buys execute immediately; they are not batched.

A3. IF a buy would take supply above `MAX_SUPPLY` (a fraction of `VIRTUAL_TOKENS`), THEN THE system SHALL refuse it with a stable error and refund the NEAR.

A4. WHEN `f` tokens are sold back in a settlement (group C), THE system SHALL compute `gross = X - ceil(k / (Y + f))`, pay `paid = gross - ceil(gross * SELL_FEE_BPS / 10000)` in total, remove `paid` from the reserve and `f` from supply. The fee stays in the reserve, so `k` never decreases: the fee raises the price for everyone who stays.

A5. THE system SHALL be solvent at all times: the NEAR needed to buy back the whole supply along the curve is never more than the reserve, and the contract's balance covers the reserve, every unclaimed payout and storage.

## B. Vintages and transfers

B1. WHEN tokens leave an account (transfer, `ft_transfer_call`, sell request), THE system SHALL take them from its newest lots first.

B2. WHEN tokens arrive in an account by transfer, THE system SHALL keep their vintages: the receiver gets lots of the same vintages, merged with its own lots of equal vintage.

B3. IF an account would hold more than `MAX_LOTS` lots, THEN THE system SHALL merge its two oldest lots into the older of the two vintages, repeatedly, until it holds `MAX_LOTS`. A merge can only make tokens older, never newer.

B4'. WHEN `ft_transfer_call` refunds `u` unused tokens, THE system SHALL take `min(u, receiver balance)` tokens from the receiver's lots **oldest first**, and give each returned token the vintage `min(its own vintage, the oldest vintage among the lots that were sent)`. No token's vintage can become newer through a refund, and the sender can never get back tokens newer than the oldest it sent.

> Replaces B4 ("return the unused tokens with the oldest vintage among the lots that were sent") after the independent review. Under B4 a colluding receiver could pass the received tokens on in `ft_on_transfer`, report them unused, and have its own older tokens credited to the sender with the newer vintage. Returning the receiver's lots newest first would instead let anyone send old tokens to a busy account (an exchange pool) that refunds, and get that account's newest tokens back. Oldest first plus the minimum closes both. Vintages are 64-bit in the contract (records, keys, views, events): with 1 ns vintages a 32-bit vintage wrapped to 0 after 2^32 ns.

B5. THE system SHALL never assign a newer vintage to existing tokens through any path: transfer, merge, refund, unfilled sell, or any combination. Only a buy from the pool creates a vintage.

B6. Splitting tokens across several accounts SHALL change nothing about when they are paid in a settlement, except rounding of at most 1 yoctoNEAR and 1 token unit per account.

## C. Sell requests, the gate and settlement

C1. WHEN an account requests to sell `a` tokens during epoch `e`, THE system SHALL lock `a` tokens from its lots (newest first, B1) into a request for epoch `e`, keeping their vintages. Requests can only be made for the current epoch.

C2. THE system SHALL settle epochs in order. Settling epoch `e` is allowed once `epoch(now) > e`. Anyone may call `settle`. Every state-changing call (buy, sell request, transfer, claim) SHALL first settle every finished epoch that has requests, so the pool state used to settle epoch `e` is the state at the end of epoch `e`, after every buy made during `e`.

C3. WHEN epoch `e` is settled, THE system SHALL compute the gate capacity `cap = floor(reserve * GATE_BPS / 10000)` and the largest token amount `F` whose payout under A4 is at most `cap` (closed form: from `gross <= cap_gross` it follows that `Y + F <= k / (X - cap_gross)`; derived exactly, with the bound proved in a test).

C4. IF the total requested in epoch `e` is at most `F`, THEN THE system SHALL fill every request in full. The gate does not bind.

C5. IF the total requested exceeds `F`, THEN THE system SHALL fill vintages from the newest to the oldest: every request token of a newer vintage is filled before any token of an older vintage. Within the one vintage where `F` runs out, every request is filled pro rata: each request is within one token unit of its pro rata share, and the shares add up exactly to that vintage's fill. Older vintages get nothing in this epoch.

> Amended on 7 November 2026: the first version rounded each pro rata share down, which creates tokens (two one-unit requests at a boundary that can fill one unit both round to zero while the curve has bought one unit). The exact split is `floor((s + a) * f / r) - floor(s * f / r)`; see [`RULES.md`](../RULES.md#change-from-the-spec).

C6. THE system SHALL pay every filled token in epoch `e` the same price: each request gets `floor(filled_tokens * total_paid / total_filled)`. Rounding dust stays in the reserve.

C7. WHEN epoch `e` is settled, THE system SHALL return every unfilled token to its owner with its original vintage. There is no carry-over: an unfilled seller requests again in a later epoch, and is then ordered by vintage again, not by when it first asked.

C8. THE work done by one settlement SHALL be bounded by the number of distinct vintages, not by the number of sellers. Per-request results are computed when the owner claims (or on its next interaction) from per-epoch, per-vintage aggregates.

C9. WHEN an owner claims a settled request, THE system SHALL pay its NEAR and credit its unfilled tokens, exactly once.

## D. Views and events

D1. THE system SHALL expose views for: parameters; pool state (`X`, `Y`, `k`, reserve, supply, spot price); the current epoch and vintage; the open requests of the current epoch grouped by vintage; each settled epoch (capacity, requested by vintage, filled by vintage, total paid, price, whether the gate bound); an account's lots and pending requests.

D2. THE system SHALL log a NEP-297 event for every buy, sell request, settlement, claim and transfer, with enough fields to rebuild the views from events alone.

## Options tested against the refresh attack (launch value 0)

Each option is a parameter fixed at initialisation (A1 applies). With the value 0 it has no effect. The reference model implements all three and the simulation tested them ([`results/REFRESH.md`](../results/REFRESH.md)); none closed the refresh attack, so they launch at 0 and the contract leaves them out.

A6. WHERE `BUY_FEE_BPS` is above 0, WHEN an account buys with `d` yoctoNEAR, THE system SHALL keep `ceil(d * BUY_FEE_BPS / 10000)` in the reserve without buying tokens with it, and compute A2 on the rest. The fee raises `k`.

C10. WHERE `YOUNG_FEE_BPS` is above 0, THE system SHALL treat the tokens of vintage `v` as young in epoch `e` when `e * EPOCH_LENGTH < (v + 1) * VINTAGE_LENGTH + YOUNG_AGE` (counted from the end of the vintage, so that every token of a vintage has the same status). IF `eff = filled * 10000 - young_filled * YOUNG_FEE_BPS` is the weighted fill of a request and `E` the sum over the epoch, THEN THE system SHALL pay the request `floor(eff * pot / E)` with `pot = floor(gross_paid * E / (total_filled * 10000))`, and remove only `pot` from the reserve. The young fee never leaves the reserve. (With `YOUNG_FEE_BPS` 0 this is C6.)

C11. WHERE `MIN_AGE` is above 0, THE system SHALL refuse a sell request for tokens of a vintage that ended less than `MIN_AGE` ago, with the stable error "tokens too young", and SHALL take the newest eligible lots first. Transfers are not restricted.

## M. Reference model and conformance

M1. A Python reference model (`lifo/`) SHALL implement groups A, B and C with Python integers, the same rounding, and the same errors.

M2. Property tests (Hypothesis) SHALL check on random sequences of buys, transfers, sell requests, settlements and claims: token conservation (balances + locked = supply); NEAR conservation (reserve + unclaimed payouts = deposits - claimed); solvency (A5); priority (C5: if a request of vintage v is not fully filled, every older vintage got nothing and every newer one was filled in full); uniform price (C6); the gate (`total_paid <= cap`); calm markets (C4); vintages never improve (B5); splitting invariance (B6); exactly-once claims (C9). They draw the three options at random (including 0) as well as the other parameters; with `YOUNG_FEE_BPS` on, each payout lies between `filled * (10000 - YOUNG_FEE_BPS) * pot / E` and `filled * 10000 * pot / E`, and NEAR conservation counts the young fee as reserve.

M3. The model SHALL emit test vectors (`vectors/*.json`): an operation sequence and the expected state after each step. The Rust contract tests SHALL replay them and agree exactly.

M4. A test SHALL show that a refresh inside a coarse vintage is filled pro rata with the other buyers of that vintage, and that with one-epoch vintages the same refresh takes the whole fill ahead of them (`tests/test_countermeasures.py`).

## S. Simulation and parameter choice

S1. An agent-based simulator SHALL run 60 days of community behaviour on the model, at `EPOCH_LENGTH` resolution, seeded and reproducible. Agents at least: early buyers who arrive in the first hours with large amounts; steady retail with random arrivals, sizes and holding times; a late wave that buys near a peak; panic sellers who sell when the price falls by a threshold; a whale or insider who dumps a large share at once; refreshers who sell old tokens and buy new ones to improve their vintage.

S2. Scenarios at least: calm growth; insider dump at the peak; panic after a shock; slow distribution by early holders; a refresh attack.

S3. Every scenario SHALL run twice on the same seed and the same agents: once with a baseline FIFO market (same curve, sells execute immediately in arrival order, no gate), once with LIFO. Show supply, reserve and price over time.

S4. Measured for each run: the late wave's average realised result per NEAR invested; early buyers' and the insider's average exit price; the largest price drop within one hour; how much of the crash-time payout went to the newest third of vintages versus the oldest third; how long the oldest holders waited; the cost of one refresh round trip against the payout priority it buys.

S5'. THE system SHALL launch with `GATE_BPS` 500, `EPOCH_LENGTH` 10 minutes, `VINTAGE_LENGTH` **24 hours**, `SELL_FEE_BPS` 50, `MAX_LOTS` 16, `VIRTUAL_NEAR` 30 NEAR, `VIRTUAL_TOKENS` 1,073,000,000 tokens with 18 decimals and `MAX_SUPPLY` 80 % of `VIRTUAL_TOKENS`.

> Replaces S5, which swept `GATE_BPS` 200, 500, 1000, 2000; `EPOCH_LENGTH` 5, 10, 30 minutes; `VINTAGE_LENGTH` 1 hour, 6 hours, 1 day; `SELL_FEE_BPS` 50, 100, 200, 300 ([`results/SWEEP.md`](../results/SWEEP.md)), where a 1-hour vintage ranked first. A 24-hour vintage costs a genuine buyer nothing (the round trip is 0.50 % at every holding time). It cut the refresh gain from +16.72 to +15.32 NEAR at the peak of the base scenario, and from +17.31 to +16.38 with the dump timed just after a vintage boundary, and one account's 16 lots cover 16 days of purchases ([`results/RECOMMENDED.md`](../results/RECOMMENDED.md)).

S6. Results (tables and figures) SHALL be written to `results/` with the command, the seed, the parameters and the commit. Figures: white background, minimal, readable at README width, no title inside the image.

S7. THE documentation SHALL state that the refresh attack is not closed: an insider who plans a dump and refreshes in the last hours before it can exit at close to the FIFO price (41.10 NEAR against 46.36 under FIFO, in the simulation), that the late group still did better than under FIFO in that case (0.449 against 0.319, 10 of 10 seeds), and that the rules tested use only token age and fees, never account identity. The README does, in [The refresh attack](../README.md#the-refresh-attack).

## Gates

| Gate | Condition | Result |
|---|---|---|
| G1 | The sweep shows a parameter set where, in the insider-dump and panic scenarios, the late wave does measurably better under LIFO than under FIFO, without making calm growth worse. | 99 of 144 sets pass ([`results/SWEEP.md`](../results/SWEEP.md)) |
| G2 | Every property in M2 holds on at least 2,000 Hypothesis examples per property. | `tests/test_properties.py` |
| G3 | The contract replays every vector exactly, and its unit tests cover every requirement in A, B and C by name. | `g3_contract_replays_every_edge_vector`, `g3_contract_replays_every_random_vector`, and the `a`, `b`, `c` tests in [`contract/tests/requirements.rs`](../contract/tests/requirements.rs) |
| G4 | A rule is adopted against the refresh only if it cuts the insider's refresh payoff below its best honest exit at both dump timings tested (peak at day 20 and at day 22), without raising the round-trip cost of a genuine buyer holding more than 24 hours and without depending on account identity. | No option tested meets it ([`results/REFRESH.md`](../results/REFRESH.md)); the 24-hour vintage was kept for its cost to genuine buyers, which is zero |
