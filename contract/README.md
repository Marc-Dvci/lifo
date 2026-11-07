# The LIFO contract

A NEAR contract (Rust, near-sdk 5) that implements the rules in [`RULES.md`](../RULES.md): one constant-product pool, tokens that keep the vintage they were bought in, sell requests batched per epoch, and a gate that pays the newest vintages first when more is asked than 5 % of the reserve. The Python model [`lifo/model.py`](../lifo/model.py) is the reference. The tests replay the model's vectors and agree on every step.

The three options of the version 2 rules (buy fee, young-token fee, minimum age) launch at 0 and are left out of this contract. There is no owner, no admin method, no upgrade method and no method that changes a parameter.

## Test

From the repository root:

```
cargo test -p lifo -j 4 -q
```

Output on this machine (Windows, rustc 1.97.1, near-sdk 5.30.1):

```
running 30 tests
..............................
test result: ok. 30 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 7.55s
running 2 tests
..
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 51.40s
```

- [`tests/requirements.rs`](tests/requirements.rs): 30 tests named by requirement, from `a1_initialisation_checks_every_parameter` to `d2_events_rebuild_every_account_lots`, plus storage and NEP-141 refusals. Every refusal is checked for its exact message and for an unchanged state.
- [`tests/vectors.rs`](tests/vectors.rs): replays [`vectors/edge.json`](../vectors/edge.json) (8 cases, 121 steps) and [`vectors/random.json`](../vectors/random.json) (200 cases, 3,578 steps). After every step it compares the result or the exact error, the reserve, the supply, the unclaimed NEAR, every account's lots and payouts, and the open requests by vintage. At the end of each case it compares every settlement and every account's pending requests. Changing the sell fee from rounded up to rounded down makes both tests fail at the first affected step.

The harness ([`tests/common/mod.rs`](tests/common/mod.rs)) runs each call like a transaction on near-sdk's mocked chain: a panic restores the storage snapshot taken before the call, as the chain reverts a failed receipt. On Windows the unit-testing feature of near-sdk builds `aws-lc-sys`, which needs NASM or its prebuilt objects; [`.cargo/config.toml`](../.cargo/config.toml) selects the prebuilt objects.

## Build

From `contract/`, with cargo-near 0.22.0:

```
cargo near build non-reproducible-wasm --no-abi
```

| File | Size | SHA-256 |
|---|---|---|
| `target/near/lifo/lifo.wasm` | 213,191 bytes | `de5cb631a9759d245d3651e30dd5eff4f5d3066a4905d5aa8a35bd62c492675a` |

Built at commit `f37070c` with rustc 1.97.1 and wasm-opt from cargo-near. The build is not reproducible (no Docker image), so another machine may produce a different hash.

## Interface

Amounts are strings (yoctoNEAR, or token units with 18 decimals). Times are nanoseconds.

| Method | Kind | What it does |
|---|---|---|
| `new(params)` | init, private | Sets every parameter once. Only the contract account can call it. |
| `buy()` | payable | Buys with the attached NEAR (A2, A3). An unregistered account pays its storage from the deposit. Returns the tokens. |
| `request_sell(amount)` | payable | Locks `amount` tokens, newest first, into a request for the current epoch (C1). Attached NEAR goes to the caller's storage balance. Returns the request id. |
| `settle()` | call | Settles the finished epoch, if any (C2 to C5). Anyone may call it. Returns 0 or 1. |
| `claim()` | call | Pays the caller's settled requests and gives back unfilled tokens (C6, C7, C9). |
| `ft_transfer`, `ft_transfer_call`, `ft_resolve_transfer`, `ft_total_supply`, `ft_balance_of` | NEP-141 | Transfers keep vintages (B1, B2); a `ft_transfer_call` refund comes out of the receiver's oldest lots and is never newer than the oldest vintage sent (B4'). |
| `ft_metadata` | NEP-148 | Name and symbol LIFO, 18 decimals. |
| `storage_deposit`, `storage_withdraw`, `storage_unregister`, `storage_balance_of`, `storage_balance_bounds` | NEP-145 | Each account pays for its own storage. |
| `contract_source_metadata` | NEP-330 | Version 0.1.0, link `https://github.com/Marc-Dvci/lifo`, standards. |
| `get_params`, `get_pool`, `get_open`, `get_settlement(epoch)`, `get_account(account_id)`, `quote_buy(deposit)`, `get_request_storage` | views | D1: parameters; X, Y, k, reserve, supply, unclaimed NEAR, locked tokens, spot price; the open epoch's requests by vintage and the current epoch and vintage; a settled epoch with unclaimed requests; an account's lots and pending requests. |

Every state-changing call first settles the finished epoch, then claims the caller's settled requests. A settled epoch's record is removed when its last request is claimed; its `settle` event keeps the record.

### Errors

The first nine are the model's: `invalid parameters`, `not launched`, `time went backwards`, `zero amount`, `buy too small`, `max supply reached`, `not enough tokens`, `sender is receiver`, `nothing to claim`. The contract adds `account not registered`, `storage not covered`, `not enough storage balance`, `account holds tokens`, `pending requests`, `attach exactly 1 yoctoNEAR`, `attach more gas` and `contract cannot hold tokens`.

### Events (NEP-297)

Standard `lifo`, version `1.0.0`. These are enough to rebuild every account's lots (`d2_events_rebuild_every_account_lots` does it).

| Event | Data |
|---|---|
| `buy` | `account_id`, `deposit`, `tokens`, `vintage`, `reserve`, `supply` |
| `sell_request` | `account_id`, `request_id`, `epoch`, `portions` (`[vintage, amount, start]`) |
| `settle` | `epoch`, `cap`, `max_fill`, `requested`, `filled` (`[vintage, amount]`), `total_requested`, `total_filled`, `paid`, `bound`, `requests`, `reserve`, `supply` |
| `claim` | `account_id`, `request_id`, `epoch`, `filled`, `paid`, `returned`, `dust_returned` |
| `transfer` | `old_owner_id`, `new_owner_id`, `lots` (`[vintage, amount]`) |

Standard `nep141` events go with them: `ft_mint` for a buy and for unfilled tokens returned at a claim, `ft_burn` for a sell request, `ft_transfer` for transfers and refunds.

## Storage

| Item | Bytes | NEAR at 10^19 yoctoNEAR per byte |
|---|---|---|
| An account (with `MAX_LOTS` 16) | 584 | 0.00584 |
| An open sell request, reserved until claimed | 2,112 | 0.02112 |

These are worst cases. The test `a5_contract_storage_is_covered_by_what_accounts_reserved` counts the bytes in storage after every call of a scenario with 16 lots per account and checks that they never exceed what the accounts reserved.

## Gas

`e2e/gas.mjs` runs on a local NEAR sandbox (nearcore 2.14.0), not testnet. Each row deploys a fresh contract with 6 second epochs and vintages; the accounts buy over the vintages, all ask to sell everything in one epoch, then one call settles it. Gas burnt by the contract's receipts, in Tgas ([`results/gas.md`](../results/gas.md), runtime 659 s):

| Vintages in the settlement | Requests | Gate bound | `settle` | `claim`, mean (max) | `request_sell`, mean (max) |
|---|---|---|---|---|---|
| 1 | 1 | yes | 1.86 | 2.40 (2.40) | 1.94 (1.94) |
| 1 | 48 | yes | 1.87 | 2.43 (2.47) | 1.96 (1.97) |
| 16 | 48 | yes | 2.35 | 2.25 (2.50) | 1.98 (2.00) |
| 48 | 48 | yes | 3.38 | 2.39 (2.65) | 2.01 (2.06) |

A settlement costs the same for 1 and 48 requests and about 0.03 Tgas more per vintage (C8). At the launch vintage of 24 hours, 48 vintages are 48 days of purchases. A `buy` cost 3.16 to 3.20 Tgas. In every row the contract's balance covered its reserve, the unclaimed payouts and its storage.

To run it:

```
bash e2e/sandbox.sh start        # in WSL or Linux; SANDBOX_DIR and GLIBC_DIR are described in the script
cd e2e && npm install && node gas.mjs
bash e2e/sandbox.sh stop
```
The gas figures above were measured on the build before 64-bit vintages and the B4' refund; they were not rerun for the current wasm.

## Initialisation and deposit checks on the sandbox

`e2e/checks.mjs` deploys the current wasm on the same local sandbox and checks, 21 of 21 passed ([`results/e2e-checks.md`](../results/e2e-checks.md)):

- `new` from another account is refused (`Method new is private`); `new` from the contract account succeeds; a second `new` is refused (`The contract has already been initialized`).
- 1 yoctoNEAR attached to `settle` or `claim` is refused (`Method settle doesn't accept deposit`); on `ft_resolve_transfer` it is refused because the method is private.
- View methods (`&self`) accept an attached deposit when called in a transaction: near-sdk adds no check to them. The yoctoNEAR stays in the contract account, and the reserve, which the contract counts itself, does not change. The script checks that for every view.
- `contract_source_metadata` returns version 0.1.0, the link `https://github.com/Marc-Dvci/lifo` and the standards nep141, nep145, nep148, nep297 and nep330.

```
bash e2e/sandbox.sh start
cd e2e && npm install && node checks.mjs
bash e2e/sandbox.sh stop
```
