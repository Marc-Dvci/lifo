// Gas of a settlement against the number of distinct vintages and of requests (requirement C8), measured on a
// local NEAR sandbox: a sandbox chain, not testnet. Writes results/gas.md and results/gas.json.
//
//   bash e2e/sandbox.sh start          (see the script for SANDBOX_DIR and GLIBC_DIR)
//   cd e2e && npm install && node gas.mjs
//
// Each configuration deploys a fresh contract with 6 second epochs and 6 second vintages, so that buys in V
// consecutive epochs make V vintages. N accounts buy, then all of them ask to sell everything in one epoch (a crash:
// the gate binds), then one call settles that epoch and each account claims.
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { actions } from 'near-api-js';
import { provider, account, call, view, createSubAccount, deploy, events, failures, gasBurnt, nearToYocto, teraToGas, sleep } from './near.js';

const RPC = process.env.RPC || 'http://127.0.0.1:3030';
const KEY_FILE = process.env.ROOT_KEY_FILE || '/tmp/lifo-sandbox/validator_key.json';
const WASM = new URL('../target/near/lifo/lifo.wasm', import.meta.url);
const OUT = new URL('../results/', import.meta.url);
const EPOCH_NS = 6_000_000_000n;
const SELLERS = 48;
const CONFIGS = [[1, 1], [1, 48], [16, 48], [48, 48]]; // [vintages, requests]
const NEAR = 10n ** 24n;
const TOKEN = 10n ** 18n;

const keyText = process.platform === 'win32'
  ? execFileSync('wsl', ['-d', process.env.WSL_DISTRO || 'Ubuntu-22.04', '--', 'cat', KEY_FILE]).toString()
  : readFileSync(KEY_FILE, 'utf8');
const key = JSON.parse(keyText);
const rpc = provider(RPC);
const root = account(key.account_id, rpc, key.secret_key);
const tgas = (g) => Number(g) / 1e12;
const git = (...a) => execFileSync('git', a, { cwd: new URL('..', import.meta.url) }).toString().trim();

/** Gas burnt by the receipts the contract executed (the call itself, without the signer's refund receipts). */
function contractGas(out, contractId) {
  let g = 0n;
  for (const r of out.receipts_outcome || []) if (r.outcome.executor_id === contractId) g += BigInt(r.outcome.gas_burnt);
  return g;
}

function must(res, what) {
  if (res.fails.length) throw new Error(`${what} failed: ${JSON.stringify(res.fails[0].failure).slice(0, 400)}`);
  return res;
}

async function epochNow(cid) {
  return Number((await view(rpc, cid, 'get_open')).current_epoch);
}

async function waitEpoch(cid, e) {
  while ((await epochNow(cid)) < e) await sleep(250);
}

async function batch(acct, cid, list) {
  const out = await acct.signAndSendTransaction({
    receiverId: cid,
    actions: list.map(([m, args, tg, dep]) => actions.functionCall(m, args, teraToGas(String(tg)), dep)),
    waitUntil: 'FINAL',
    throwOnFailure: false,
  });
  return { out, fails: failures(out), events: events(out), gas: gasBurnt(out) };
}

const stats = (xs) => {
  const v = xs.map(tgas);
  return { min: Math.min(...v), mean: v.reduce((a, b) => a + b, 0) / v.length, max: Math.max(...v) };
};

async function runConfig(prefix, sellers, [V, N]) {
  const name = `${prefix}v${V}n${N}`;
  const { id: cid, secretKey } = await createSubAccount(root, name, '30');
  const block = await rpc.viewBlock({ finality: 'final' });
  const params = {
    virtual_near: String(30n * NEAR),
    virtual_tokens: String(1_073_000_000n * TOKEN),
    max_supply: String((1_073_000_000n * TOKEN * 8n) / 10n),
    gate_bps: 500,
    sell_fee_bps: 50,
    epoch_length: String(EPOCH_NS),
    vintage_length: String(EPOCH_NS),
    launch_time: String(BigInt(block.header.timestamp_nanosec)),
    max_lots: 16,
  };
  await deploy(account(cid, rpc, secretKey), readFileSync(WASM), { method: 'new', args: { params } });
  const who = sellers.slice(0, N);
  const start = (await epochNow(cid)) + 1;
  const tokens = new Map();
  const buyGas = [];
  for (let j = 0; j < V; j++) {
    await waitEpoch(cid, start + j);
    const group = who.filter((_, i) => Math.floor((i * V) / N) === j);
    const res = await Promise.all(group.map((s) => batch(s, cid, [
      ['storage_deposit', {}, 20, nearToYocto('0.1')],
      ['buy', {}, 50, nearToYocto('1')],
    ])));
    res.forEach((r, k) => {
      must(r, `buy by ${group[k].accountId}`);
      tokens.set(group[k].accountId, r.events.find((e) => e.event === 'buy').data[0].tokens);
      buyGas.push(contractGas(r.out, cid));
    });
  }
  const askEpoch = (await epochNow(cid)) + 1;
  await waitEpoch(cid, askEpoch);
  const asks = await Promise.all(who.map((s) => call(s, cid, 'request_sell', { amount: tokens.get(s.accountId) }, { gas: teraToGas('100') })));
  asks.forEach((r, k) => must(r, `request by ${who[k].accountId}`));
  const askEpochs = new Set(asks.map((r) => r.events.find((e) => e.event === 'sell_request').data[0].epoch));
  if (askEpochs.size !== 1) throw new Error(`requests spread over epochs ${[...askEpochs]}: lengthen EPOCH_NS`);
  const settledEpoch = Number([...askEpochs][0]);
  await waitEpoch(cid, settledEpoch + 1);
  const settle = must(await call(root, cid, 'settle', {}, { gas: teraToGas('300') }), 'settle');
  const ev = settle.events.find((e) => e.event === 'settle')?.data[0];
  if (!ev) throw new Error('settle did not settle');
  const claims = await Promise.all(who.map((s) => call(s, cid, 'claim', {}, { gas: teraToGas('100') })));
  claims.forEach((r, k) => must(r, `claim by ${who[k].accountId}`));
  const pool = await view(rpc, cid, 'get_pool');
  const acc = await rpc.viewAccount({ accountId: cid });
  const amount = BigInt(acc.amount);
  const storageUsage = BigInt(acc.storage_usage ?? acc.storageUsage);
  const owed = BigInt(pool.reserve) + BigInt(pool.unclaimed_near) + storageUsage * 10n ** 19n;
  return {
    vintages_planned: V,
    requests: N,
    vintages_in_settlement: ev.requested.length,
    gate_bound: ev.bound,
    settle_tgas: tgas(contractGas(settle.out, cid)),
    settle_tx_tgas: tgas(settle.gas),
    request_tgas: stats(asks.map((r) => contractGas(r.out, cid))),
    claim_tgas: stats(claims.map((r) => contractGas(r.out, cid))),
    buy_tgas: stats(buyGas),
    balance_covers_reserve_unclaimed_and_storage: amount >= owed,
    balance_yocto: String(amount),
    reserve_plus_unclaimed_plus_storage_yocto: String(owed),
  };
}

async function main() {
  const began = Date.now();
  const status = await (await fetch(RPC, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'status', params: [] }) })).json();
  const prefix = `g${Date.now() % 1_000_000}`;
  const sellers = [];
  for (let i = 0; i < SELLERS; i++) {
    const { id, secretKey } = await createSubAccount(root, `${prefix}s${i}`, '5');
    sellers.push(account(id, rpc, secretKey));
  }
  const rows = [];
  for (const c of CONFIGS) {
    const row = await runConfig(prefix, sellers, c);
    console.log(JSON.stringify(row));
    rows.push(row);
  }
  const meta = {
    command: 'cd e2e && node gas.mjs',
    commit: git('rev-parse', '--short', 'HEAD') + (git('status', '--porcelain', 'contract').length ? '-dirty' : ''),
    sandbox: status.result?.version?.version,
    wasm_sha256: createHash('sha256').update(readFileSync(WASM)).digest('hex'),
    runtime_s: Math.round((Date.now() - began) / 1000),
    epoch_s: Number(EPOCH_NS / 1_000_000_000n),
  };
  writeFileSync(new URL('gas.json', OUT), JSON.stringify({ ...meta, rows }, null, 2) + '\n');
  const f = (x) => x.toFixed(2);
  const lines = [
    '# Gas of a settlement',
    '',
    `Measured on a local NEAR sandbox (nearcore ${meta.sandbox}), not on testnet. Command: \`${meta.command}\` after \`bash e2e/sandbox.sh start\`. Commit \`${meta.commit}\`. Wasm SHA-256 \`${meta.wasm_sha256}\`. Runtime ${meta.runtime_s} s.`,
    '',
    `Each row is a fresh contract on the launch curve with ${meta.epoch_s} s epochs and ${meta.epoch_s} s vintages. The accounts buy 1 NEAR each spread over the vintages, then all ask to sell everything in one epoch; one \`settle\` call settles it; each account then claims. Gas is what the contract's receipts burnt, in Tgas (the limit per call is 300).`,
    '',
    '| vintages in the settlement | requests | gate bound | settle | claim, mean (max) | request, mean (max) | balance covers reserve, payouts and storage |',
    '|---|---|---|---|---|---|---|',
    ...rows.map((r) => `| ${r.vintages_in_settlement} | ${r.requests} | ${r.gate_bound ? 'yes' : 'no'} | ${f(r.settle_tgas)} | ${f(r.claim_tgas.mean)} (${f(r.claim_tgas.max)}) | ${f(r.request_tgas.mean)} (${f(r.request_tgas.max)}) | ${r.balance_covers_reserve_unclaimed_and_storage ? 'yes' : 'no'} |`),
    '',
  ];
  writeFileSync(new URL('gas.md', OUT), lines.join('\n'));
  console.log(lines.join('\n'));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
