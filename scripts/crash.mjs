// A scripted crash on one deployment, with the four demo accounts.
//
//   node scripts/crash.mjs --network sandbox --keys .guest/keys-sandbox.json
//   node scripts/crash.mjs --network testnet --keys .guest/keys-testnet.json --rpc https://rpc.testnet.near.org
//
// Plates (vintages) are made on the contract's own clock: the early buyer and the insider buy in a first vintage, the
// early buyer buys a little more in the next, the late buyer and the panic seller buy in a third. In the last round of
// that third vintage the insider, the late buyer and the panic seller all ask to sell everything at once: the gate
// binds, the newest plate is paid first and the insider's older plate waits. Then the round is settled and each
// account claims, and the insider asks again to show that the oldest plate is paid in the next round.
// Every transaction is printed and written to results/crash-<network>.json. Needs a demo-speed deployment
// (plates of 10 minutes or less); on the launch rules the same script would take days.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { provider, account, call, view, nearToYocto, teraToGas, sleep } from '../e2e/near.js';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const network = arg('network', 'sandbox');
const keysFile = arg('keys', `.guest/keys-${network}.json`);
const which = arg('d', 'demo');
const keys = JSON.parse(readFileSync(keysFile, 'utf8'));
const rpcUrl = arg('rpc', keys.rpc);
const outFile = new URL(`../results/crash-${network}.json`, import.meta.url);
const cid = keys.vars[which === 'demo' ? 'CONTRACT_DEMO' : 'CONTRACT_LAUNCH'];
const rpc = provider(rpcUrl);
const roles = ['early', 'late', 'insider', 'panic'];
const acct = Object.fromEntries(roles.map((r) => [r, account(keys.vars[`ROLE_${r.toUpperCase()}`], rpc, keys.keys[`KEY_${r.toUpperCase()}`])]));
const NEAR = (n) => nearToYocto(String(n));
const SELL_STORAGE = nearToYocto('0.025');

const params = await view(rpc, cid, 'get_params');
const epochS = Number(BigInt(params.epoch_length) / 1_000_000_000n);
const vintageS = Number(BigInt(params.vintage_length) / 1_000_000_000n);
if (vintageS > 600) throw new Error(`plates of ${vintageS} s: use the demo-speed deployment (--d demo)`);
const perVintage = vintageS / epochS;

const started = Date.now();
const steps = [];
const rounds = [];
const paidBy = Object.fromEntries(roles.map((r) => [r, 0n]));
const spentBy = Object.fromEntries(roles.map((r) => [r, 0n]));
const stamp = () => `+${String(Math.round((Date.now() - started) / 1000)).padStart(3, ' ')}s`;
const open = () => view(rpc, cid, 'get_open');

async function waitFor(test, label) {
  for (;;) {
    const o = await open();
    if (test(o)) return o;
    await sleep(1000);
  }
}

async function step(role, method, args, deposit = 0n, note = '') {
  deposit = BigInt(deposit);
  const res = await call(acct[role], cid, method, args, { deposit, gas: teraToGas('100') });
  const error = res.fails.length ? JSON.stringify(res.fails[0].failure?.ActionError?.kind?.FunctionCallError?.ExecutionError ?? res.fails[0].failure).slice(0, 160) : null;
  for (const e of res.events) if (e.event === 'settle') for (const d of e.data) rounds.push({ ...d, hash: res.hash });
  for (const e of res.events) {
    if (e.event === 'claim') for (const d of e.data) if (d.account_id === acct[role].accountId) paidBy[role] += BigInt(d.paid);
  }
  if (!error && method === 'buy') spentBy[role] += deposit;
  const row = { t: Math.round((Date.now() - started) / 1000), role, method, args, deposit: String(deposit), hash: res.hash, ok: !error, error, note, events: res.events.map((e) => e.event) };
  steps.push(row);
  console.log(`${stamp()}  ${role.padEnd(8)} ${method.padEnd(12)} ${note.padEnd(34)} ${error ? `REFUSED ${error}` : 'ok'}  tx ${res.hash}`);
  return { ...res, error };
}

const held = async (role) => {
  const a = await view(rpc, cid, 'get_account', { account_id: acct[role].accountId });
  return a ? a.lots.reduce((n, [, x]) => n + BigInt(x), 0n) : 0n;
};
const sellAll = async (role, note) => {
  const amount = await held(role);
  if (amount === 0n) { console.log(`${stamp()}  ${role.padEnd(8)} holds nothing, no sell`); return; }
  await step(role, 'request_sell', { amount: String(amount) }, SELL_STORAGE, note);
};

/** The Worker's cron settles a finished round and keeps it in the page's log. Give it 90 s, then settle here. */
async function settleOrWait(epoch) {
  console.log(`${stamp()}  round ${epoch} is over: waiting up to 90 s for the Worker's cron to settle it`);
  for (let i = 0; i < 90; i++) {
    const o = await open();
    if (o.epoch === null || Number(o.epoch) !== epoch) {
      console.log(`${stamp()}  round ${epoch} was settled by someone else (the cron)`);
      return;
    }
    await sleep(1000);
  }
  await step('early', 'settle', {}, 0n, 'cron did not, so settle here');
}

console.log(`crash on ${cid} (${network}), rounds of ${epochS} s, plates of ${vintageS} s`);
const v0 = Number((await open()).current_vintage) + 1;
console.log(`${stamp()}  waiting for plate ${v0} to open`);

// 1. The first plate: the early buyer and the insider.
await waitFor((o) => Number(o.current_vintage) >= v0);
await step('early', 'buy', {}, NEAR(2), `plate ${v0}, early buyer`);
await step('insider', 'buy', {}, NEAR(3), `plate ${v0}, insider`);
// 2. A second plate: the early buyer adds a little.
await waitFor((o) => Number(o.current_vintage) >= v0 + 1);
await step('early', 'buy', {}, NEAR(1), `plate ${v0 + 1}, early buyer`);
// 3. The late wave in the third plate.
await waitFor((o) => Number(o.current_vintage) >= v0 + 2);
await step('late', 'buy', {}, NEAR(1), `plate ${v0 + 2}, late wave`);
await step('late', 'buy', {}, NEAR(1), `plate ${v0 + 2}, late wave`);
await step('panic', 'buy', {}, NEAR(1), `plate ${v0 + 2}, late wave`);
await step('panic', 'buy', {}, NEAR(1), `plate ${v0 + 2}, late wave`);
// 4. In the last round of that plate: everyone asks to sell everything.
const dumpEpoch = (v0 + 2) * perVintage + (perVintage - 1);
console.log(`${stamp()}  waiting for round ${dumpEpoch}, the last of plate ${v0 + 2}`);
await waitFor((o) => Number(o.current_epoch) >= dumpEpoch);
await sellAll('insider', 'insider dumps everything');
await sellAll('panic', 'panic seller sells everything');
await sellAll('late', 'late buyer sells everything');
const asked = await open();
console.log(`${stamp()}  open round ${asked.epoch}: ${asked.requests} requests, by plate ${JSON.stringify(asked.requested.map(([v, a]) => [v, (BigInt(a) / 10n ** 18n).toString()]))}`);
// 5. Settle and claim.
await waitFor((o) => Number(o.current_epoch) > dumpEpoch);
await settleOrWait(dumpEpoch);
for (const r of roles) {
  const a = await view(rpc, cid, 'get_account', { account_id: acct[r].accountId });
  if (a && a.pending.length) await step(r, 'claim', {}, 0n, 'pays and returns the unfilled');
}
// 6. The insider asks again for what is left.
await sellAll('insider', 'insider asks again');
await waitFor((o) => Number(o.current_epoch) > dumpEpoch + 1);
await settleOrWait(dumpEpoch + 1);
for (const r of roles) {
  const a = await view(rpc, cid, 'get_account', { account_id: acct[r].accountId });
  if (a && a.pending.length) await step(r, 'claim', {}, 0n, 'pays and returns the unfilled');
}

const pool = await view(rpc, cid, 'get_pool');
const final = {};
for (const r of roles) {
  const a = await view(rpc, cid, 'get_account', { account_id: acct[r].accountId });
  final[r] = { lots: a?.lots ?? [], paid_out: a?.paid_out ?? '0', near_spent: String(spentBy[r]), near_paid_by_my_claims: String(paidBy[r]) };
}
const git = (...a) => execFileSync('git', a, { cwd: new URL('..', import.meta.url) }).toString().trim();
mkdirSync(new URL('../results/', import.meta.url), { recursive: true });
writeFileSync(outFile, JSON.stringify({
  command: `node scripts/crash.mjs ${process.argv.slice(2).join(' ')}`,
  seed: 'none: scripted, no random choice',
  git_commit: git('rev-parse', '--short', 'HEAD'),
  network, rpc: rpcUrl, contract: cid, params,
  started: new Date(started).toISOString(), finished: new Date().toISOString(),
  steps, settled_rounds: rounds, final_pool: pool, final,
}, null, 2));

const near = (y) => (Number(BigInt(y) / 10n ** 18n) / 1e6).toFixed(3);
console.log('\nsettled rounds (from the settle events):');
for (const r of rounds) console.log(`  round ${r.epoch}: paid ${near(r.paid)} NEAR, cap ${near(r.cap)} NEAR, gate ${r.bound ? 'bound' : 'did not bind'}, requested by plate ${JSON.stringify(r.requested.map(([v, a]) => [v, (BigInt(a) / 10n ** 18n).toString()]))}, filled ${JSON.stringify(r.filled.map(([v, a]) => [v, (BigInt(a) / 10n ** 18n).toString()]))}`);
console.log('\nper account (NEAR spent on buys in this run, NEAR paid back in total by the contract, tokens still held):');
for (const r of roles) console.log(`  ${r.padEnd(8)} ${near(spentBy[r]).padStart(8)} ${near(final[r].paid_out).padStart(8)}  ${(final[r].lots.reduce((n, [, x]) => n + BigInt(x), 0n) / 10n ** 18n).toString()} LIFO`);
console.log(`\nwrote results/crash-${network}.json`);
