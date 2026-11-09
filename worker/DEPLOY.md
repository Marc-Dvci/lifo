# Deploying the demo server

These steps put the live demo online on 8 November 2026: the contracts `launch.lifo.testnet` and
`demo.lifo.testnet`, the Worker `lifo-demo` and the page at [lifo-amt.pages.dev](https://lifo-amt.pages.dev). The same
steps run on a local sandbox with `--network sandbox`.

## What is deployed

- Two contracts from the same wasm (`target/near/lifo/lifo.wasm`, SHA-256 `de5cb631a9759d245d3651e30dd5eff4f5d3066a4905d5aa8a35bd62c492675a`): `launch.<parent>` (rounds of 10 minutes, plates of 24 hours) and `demo.<parent>` (rounds of 1 minute, plates of 3 minutes). Same curve, gate and fee in both: 30 NEAR and 1,073,000,000 tokens virtual, 80 % max supply, gate 500 bps, sell fee 50 bps, 16 lots.
- Five accounts the Worker signs for: `early`, `late`, `insider`, `panic` (the roles a visitor plays) and `crank` (settles rounds). The contract accounts have no key after `new`.
- One Worker (`worker/`): `/api`, one Durable Object (SQLite-backed, free plan) and one cron trigger (every minute).
- One Pages project (`pages/`): serves `web/` and hands `/api/*` to the Worker through a service binding, so the page and its API share one public address.

## 1. Parent account

A funded testnet account that owns the others; the live demo uses `lifo.testnet`. With another name, edit the `[vars]` of `worker/wrangler.toml` to the ids that step 2 prints.

Funding needed with the defaults: 5 NEAR for each contract, 40 NEAR for each of the four roles, 5 NEAR for the crank, so 175 NEAR plus gas. Lower the role funding with `--role-near 10` if the parent has less; a role that runs out makes its buy fail with the contract's own message.

## 2. Accounts, contracts, keys (from the repository root)

```
$env:PARENT_ID = "lifo.testnet"
$env:PARENT_KEY = "ed25519:..."          # the parent's secret key
node scripts/provision.mjs --network testnet
```

It creates the seven sub-accounts, deploys the wasm on `launch` and `demo`, calls `new` with each deployment's parameters, reads `get_params` back, **deletes the full-access key of both contract accounts** and reads the key list back (it stops with an error if any key remains). It prints `0 keys left` for each. It writes:

- `.guest/keys-testnet.json` (keys of the roles and the crank, for `scripts/crash.mjs`),
- `.guest/secrets-testnet.json` (the five secrets for step 3).

Both stay out of git. Options: `--rpc <url>`, `--role-near`, `--contract-near`, `--crank-near`, `--wasm <path>`.

The clocks of both deployments start when `new` runs.

## 3. Secrets (names only in code: `KEY_EARLY`, `KEY_LATE`, `KEY_INSIDER`, `KEY_PANIC`, `KEY_CRANK`)

```
cd worker
npm install
npx wrangler login
npx wrangler secret bulk ../.guest/secrets-testnet.json
```

or one at a time: `npx wrangler secret put KEY_EARLY`, then `KEY_LATE`, `KEY_INSIDER`, `KEY_PANIC`, `KEY_CRANK`, pasting the value from `.guest/secrets-testnet.json`.

## 4. Deploy the Worker, then the page

```
cd worker
npx wrangler deploy
cd ../pages
npx wrangler pages project create lifo --production-branch main
npx wrangler pages deploy --branch main
```

The first Worker deploy applies the Durable Object migration (`new_sqlite_classes = ["Gate"]`) and registers the cron `* * * * *`. The bundle is 23.84 KiB (7.72 KiB gzipped), with no dependency. Cloudflare gives the Pages project its address; for this demo it is `lifo-amt.pages.dev`.

`RPC_URL` in `wrangler.toml` lists three testnet endpoints. A call that gets a rate limit (HTTP 429), a server error or a network failure moves to the next one, and the endpoint that answered is tried first by the next call. If none answers, `/api/state` serves the last state it read, for up to 2 minutes, with its age in `stale_ms`.

## 5. Check

```
curl https://lifo-amt.pages.dev/api/state?d=demo
```

It returns the pool, the open round, the four accounts and the logs. `npx wrangler tail lifo-demo` shows the cron running every minute. Then, from the repository root, the scripted crash (about 10 minutes at demo speed):

```
node scripts/crash.mjs --network testnet --keys .guest/keys-testnet.json --rpc https://test.rpc.fastnear.com
```

It writes `results/crash-testnet.json` with every transaction hash.

## Free-plan limits and what the Worker uses

| Limit (Workers Free) | Use |
|---|---|
| 10 ms CPU per request | About 0.13 ms of CPU for building, signing and parsing one transaction (`node worker/bench.mjs`, Node, not a Cloudflare figure). The rest of a request waits on the RPC. |
| 50 external subrequests per invocation | `/api/state`: 6 RPC calls, plus one per finished round that a demo role has not claimed yet (at most 6), plus one per endpoint skipped after a refusal. An action: 3 to 4 (nonce, block, send, and one view for a sell). Every invocation stops itself at 45. |
| 100,000 requests per day | The page polls every 5 s while the tab is visible and stops 15 minutes after the last click. State answers are cached 4 s inside an isolate. Static files do not count. |
| 100,000 Durable Object requests per day | One per action, per cron minute (1,440) and per uncached state read. |
| 5 cron triggers | 1 used. |

Rate limits on actions: 8 per visitor per 10 minutes and 120 per hour for the whole demo (`worker/src/limits.js`). A visitor is stored as a truncated hash of the IP address.
