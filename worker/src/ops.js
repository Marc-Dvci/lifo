// What the demo server does: read the views the page needs, act for a demo role, and the once-a-minute tick that
// settles rounds and claims for the demo roles. Everything reaches the chain through `ctx.rpc`, the key through
// `ctx.signer(name)` and the state through `ctx.store`, so the tests run with fakes.

import { sendCall } from './chain.js';
import { LIMITS, RateLimiter, ipKey } from './limits.js';
import { DEPLOYMENTS, ROLE_NAMES } from './routes.js';

const NEAR = 10n ** 24n;
const near = (n) => BigInt(Math.round(n * 1e6)) * 10n ** 18n;
const TGAS = 10n ** 12n;

/** The fixed amounts of each role. A sell asks to sell this share of the role's held tokens. */
export const PLAN = {
  early: { buy: near(2), sellPct: 50n },
  late: { buy: near(1), sellPct: 100n },
  insider: { buy: near(3), sellPct: 100n },
  panic: { buy: near(1), sellPct: 100n },
};
// A sell request reserves 0.0211 NEAR of storage until it is claimed (the contract's get_request_storage).
const SELL_STORAGE = near(0.025);
const KEEP = 30; // rounds and actions kept per deployment

export const NO_ROLE_KEY = 'no key for this role';

export function config(env) {
  return {
    network: env.NETWORK || 'testnet',
    contracts: { launch: env.CONTRACT_LAUNCH, demo: env.CONTRACT_DEMO },
    roles: { early: env.ROLE_EARLY, late: env.ROLE_LATE, insider: env.ROLE_INSIDER, panic: env.ROLE_PANIC },
    crank: env.CRANK_ACCOUNT,
    explorer: { tx: env.EXPLORER_TX || '', account: env.EXPLORER_ACCOUNT || '' },
  };
}

const sum = (pairs) => pairs.reduce((n, [, a]) => n + BigInt(a), 0n);

/** The views of one deployment in the shape the page reads, with the logs kept by the Durable Object. */
export async function readState(ctx, d) {
  const cfg = config(ctx.env);
  const cid = cfg.contracts[d];
  const [params, pool, open, ...accounts] = await Promise.all([
    ctx.params(d),
    ctx.rpc.view(cid, 'get_pool'),
    ctx.rpc.view(cid, 'get_open'),
    ...ROLE_NAMES.map((r) => ctx.rpc.view(cid, 'get_account', { account_id: cfg.roles[r] })),
  ]);
  const log = await ctx.getLog(d);
  return {
    d,
    network: cfg.network,
    contract: cid,
    explorer: cfg.explorer,
    now: ctx.now(),
    params,
    pool,
    open,
    accounts: Object.fromEntries(ROLE_NAMES.map((r, i) => [r, { id: cfg.roles[r], view: accounts[i] }])),
    plan: Object.fromEntries(ROLE_NAMES.map((r) => [r, { buy_near: String(PLAN[r].buy / NEAR), sell_pct: Number(PLAN[r].sellPct) }])),
    rounds: log.rounds,
    feed: log.feed,
  };
}

const pick = (e) => ({
  epoch: e.epoch, cap: e.cap, requested: e.requested, filled: e.filled, total_requested: e.total_requested,
  total_filled: e.total_filled, paid: e.paid, bound: e.bound, requests: e.requests, reserve: e.reserve, supply: e.supply,
});

/** Keeps the settled rounds (from `settle` events) and the latest actions of a deployment. */
export async function record(store, d, entry, events, hash, at) {
  const rounds = (await store.get(`rounds:${d}`)) || [];
  for (const e of events) {
    if (e.event !== 'settle') continue;
    for (const data of e.data || []) {
      if (!rounds.some((r) => r.epoch === data.epoch)) rounds.push({ ...pick(data), hash, at });
    }
  }
  rounds.sort((a, b) => Number(b.epoch) - Number(a.epoch));
  await store.put(`rounds:${d}`, rounds.slice(0, KEEP));
  if (entry) {
    const feed = (await store.get(`feed:${d}`)) || [];
    feed.unshift(entry);
    await store.put(`feed:${d}`, feed.slice(0, KEEP));
  }
}

const detailOf = (events) => {
  for (const e of events) {
    const x = e.data?.[0];
    if (e.event === 'buy') return { deposit: x.deposit, tokens: x.tokens, vintage: x.vintage };
    if (e.event === 'sell_request') return { amount: String(sum(x.portions.map((p) => [p[0], p[1]]))), epoch: x.epoch };
  }
  return {};
};

export async function act(ctx, { d, role, action, ip }) {
  if (!DEPLOYMENTS.includes(d) || !ROLE_NAMES.includes(role)) return { status: 400, body: { error: 'bad request' } };
  const cfg = config(ctx.env);
  const now = ctx.now();
  const limiter = new RateLimiter(LIMITS, (await ctx.store.get('rl')) || undefined);
  const taken = limiter.take(await ipKey(ip), now);
  await ctx.store.put('rl', limiter.state);
  if (!taken.ok) {
    const who = taken.scope === 'visitor' ? 'You have used your actions for now' : 'The demo has used its actions for this hour';
    return { status: 429, body: { error: `${who}. Try again in ${taken.retryAfter} s.`, scope: taken.scope, retry_after: taken.retryAfter } };
  }
  const signer = await ctx.signer(role);
  if (!signer) return { status: 503, body: { error: 'this demo role is not set up' } };
  const contractId = cfg.contracts[d];
  const signerId = cfg.roles[role];
  let call;
  if (action === 'buy') {
    call = { method: 'buy', args: {}, gas: 50n * TGAS, deposit: PLAN[role].buy };
  } else {
    const acct = await ctx.rpc.view(contractId, 'get_account', { account_id: signerId });
    const held = acct ? sum(acct.lots) : 0n;
    const amount = (held * PLAN[role].sellPct) / 100n;
    if (amount === 0n) return { status: 409, body: { error: `${role} holds nothing to sell here` } };
    call = { method: 'request_sell', args: { amount: String(amount) }, gas: 50n * TGAS, deposit: SELL_STORAGE };
  }
  let sent;
  try {
    sent = await sendCall(ctx.rpc, signer, { signerId, receiverId: contractId, ...call });
  } catch {
    return { status: 503, body: { error: 'the chain did not take the transaction, try again' } };
  }
  const entry = { at: now, role, action, ok: !sent.error, hash: sent.hash, error: sent.error || undefined, ...(sent.error ? {} : detailOf(sent.events)) };
  await record(ctx.store, d, entry, sent.events, sent.hash, now);
  return { status: 200, body: { ok: !sent.error, hash: sent.hash, role, action, d, error: sent.error || undefined } };
}

/**
 * Once a minute: settle a finished round with the crank account, then claim for each demo role that has a settled
 * request. Both deployments; one failure does not stop the other. Worst case about 40 requests to the RPC.
 */
export async function tick(ctx) {
  const cfg = config(ctx.env);
  const report = [];
  let blockHash;
  const getBlock = async () => (blockHash ??= await ctx.rpc.blockHash());
  for (const d of DEPLOYMENTS) {
    const cid = cfg.contracts[d];
    if (!cid) continue;
    const done = { d, settled: false, claimed: [], errors: [] };
    report.push(done);
    try {
      const open = await ctx.rpc.view(cid, 'get_open');
      const current = BigInt(open.current_epoch);
      if (open.epoch !== null && BigInt(open.epoch) < current) {
        const crank = await ctx.signer('crank');
        if (!crank) throw new Error(NO_ROLE_KEY);
        const sent = await sendCall(ctx.rpc, crank, { signerId: cfg.crank, receiverId: cid, method: 'settle', args: {}, gas: 100n * TGAS, deposit: 0n, blockHash: await getBlock() });
        done.settled = !sent.error;
        if (sent.error) done.errors.push(sent.error);
        await record(ctx.store, d, { at: ctx.now(), role: 'crank', action: 'settle', ok: !sent.error, hash: sent.hash, error: sent.error || undefined }, sent.events, sent.hash, ctx.now());
      }
      const accounts = await Promise.all(ROLE_NAMES.map((r) => ctx.rpc.view(cid, 'get_account', { account_id: cfg.roles[r] })));
      for (const [i, role] of ROLE_NAMES.entries()) {
        const a = accounts[i];
        if (!a || !a.pending.some((p) => BigInt(p.epoch) < current)) continue;
        const signer = await ctx.signer(role);
        if (!signer) continue;
        const sent = await sendCall(ctx.rpc, signer, { signerId: cfg.roles[role], receiverId: cid, method: 'claim', args: {}, gas: 100n * TGAS, deposit: 0n, blockHash: await getBlock() });
        if (sent.error) done.errors.push(`${role}: ${sent.error}`);
        else done.claimed.push(role);
        await record(ctx.store, d, { at: ctx.now(), role, action: 'claim', ok: !sent.error, hash: sent.hash, error: sent.error || undefined }, sent.events, sent.hash, ctx.now());
      }
    } catch (e) {
      done.errors.push(String(e?.message || e).slice(0, 200));
    }
  }
  return report;
}

export { NEAR };
