import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { base58Encode, fromBase64, signerFromSecret } from '../src/chain.js';
import { act, readState, tick } from '../src/ops.js';

const env = {
  NETWORK: 'sandbox', CONTRACT_LAUNCH: 'launch.t.near', CONTRACT_DEMO: 'demo.t.near',
  ROLE_EARLY: 'early.t.near', ROLE_LATE: 'late.t.near', ROLE_INSIDER: 'insider.t.near', ROLE_PANIC: 'panic.t.near',
  CRANK_ACCOUNT: 'crank.t.near',
};

async function fakeSigner() {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const seed = privateKey.export({ format: 'der', type: 'pkcs8' }).subarray(-32);
  const pub = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  return signerFromSecret(`ed25519:${base58Encode(Uint8Array.from([...seed, ...pub]))}`);
}

const BLOCK = base58Encode(new Uint8Array(32).fill(5));

/** A chain that answers views from `views[contract][method]` and records every transaction it is sent. */
async function setup({ views = {}, outcome } = {}) {
  const sent = [];
  const signer = await fakeSigner();
  const rpc = {
    async view(cid, method, args) {
      const v = views[cid]?.[method];
      return typeof v === 'function' ? v(args) : v;
    },
    blockHash: async () => BLOCK,
    nonce: async () => 10n,
    async call(method, params) {
      assert.equal(method, 'send_tx');
      sent.push(Buffer.from(fromBase64(params.signed_tx_base64)).toString('latin1'));
      return outcome ? outcome(sent.length) : { status: { SuccessValue: '' }, receipts_outcome: [] };
    },
  };
  const data = new Map();
  let clock = 1_000_000;
  const ctx = {
    env, rpc, sent, data,
    store: { get: async (k) => data.get(k), put: async (k, v) => void data.set(k, structuredClone(v)) },
    now: () => clock,
    signer: async () => signer,
    advance: (ms) => { clock += ms; },
  };
  return ctx;
}

const eventLog = (event, d) => `EVENT_JSON:${JSON.stringify({ standard: 'lifo', version: '1.0.0', event, data: [d] })}`;
const okWith = (...logs) => () => ({ status: { SuccessValue: '' }, receipts_outcome: [{ outcome: { logs, status: { SuccessValue: '' } } }] });

test('a buy sends the role its fixed amount to the chosen contract and the page can read it back', async () => {
  const ctx = await setup({ outcome: okWith(eventLog('buy', { account_id: 'early.t.near', deposit: '2', tokens: '777', vintage: 4 })) });
  const r = await act(ctx, { d: 'demo', role: 'early', action: 'buy', ip: '1.1.1.1' });
  assert.equal(r.status, 200);
  assert.equal(r.body.ok, true);
  assert.match(r.body.hash, /^[1-9A-HJ-NP-Za-km-z]{43,44}$/);
  assert.equal(ctx.sent.length, 1);
  assert.ok(ctx.sent[0].includes('early.t.near') && ctx.sent[0].includes('demo.t.near') && ctx.sent[0].includes('buy'));
  const feed = ctx.data.get('feed:demo');
  assert.deepEqual([feed[0].role, feed[0].action, feed[0].tokens, feed[0].vintage, feed[0].hash], ['early', 'buy', '777', 4, r.body.hash]);
  assert.equal(ctx.data.get('feed:launch'), undefined);
});

test('a sell asks for the role share of what it holds and attaches the request storage', async () => {
  const ctx = await setup({ views: { 'demo.t.near': { get_account: { lots: [[3, '1000'], [4, '500']], pending: [] } } } });
  const r = await act(ctx, { d: 'demo', role: 'early', action: 'sell', ip: 'a' });
  assert.equal(r.status, 200);
  assert.ok(ctx.sent[0].includes('request_sell') && ctx.sent[0].includes('"amount":"750"'));
  const all = await act(ctx, { d: 'demo', role: 'panic', action: 'sell', ip: 'b' });
  assert.equal(all.status, 200);
  assert.ok(ctx.sent[1].includes('"amount":"1500"'));
});

test('a sell by a role that holds nothing is refused before anything is signed', async () => {
  const ctx = await setup({ views: { 'demo.t.near': { get_account: null } } });
  const r = await act(ctx, { d: 'demo', role: 'late', action: 'sell', ip: 'a' });
  assert.equal(r.status, 409);
  assert.equal(ctx.sent.length, 0);
});

test('a visitor gets 8 actions in 10 minutes, then a 429 with the wait, and nothing is signed for the refused one', async () => {
  const ctx = await setup();
  for (let i = 0; i < 8; i++) assert.equal((await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: '9.9.9.9' })).status, 200);
  const r = await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: '9.9.9.9' });
  assert.equal(r.status, 429);
  assert.equal(r.body.scope, 'visitor');
  assert.ok(r.body.retry_after > 0);
  assert.equal(ctx.sent.length, 8);
  assert.equal((await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: '8.8.8.8' })).status, 200);
});

test('the whole demo stops at 120 actions an hour whoever asks', async () => {
  const ctx = await setup();
  for (let i = 0; i < 120; i++) assert.equal((await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: `ip${i}` })).status, 200);
  const r = await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: 'fresh' });
  assert.deepEqual([r.status, r.body.scope], [429, 'demo']);
});

test('a transaction the contract refuses is reported with its message and kept in the log as failed', async () => {
  const fail = () => ({ status: { Failure: {} }, receipts_outcome: [{ outcome: { logs: [], status: { Failure: { ActionError: { kind: { FunctionCallError: { ExecutionError: 'Smart contract panicked: buy too small' } } } } } } }] });
  const ctx = await setup({ outcome: fail });
  const r = await act(ctx, { d: 'launch', role: 'insider', action: 'buy', ip: 'a' });
  assert.deepEqual([r.status, r.body.ok, r.body.error], [200, false, 'Smart contract panicked: buy too small']);
  assert.equal(ctx.data.get('feed:launch')[0].ok, false);
});

test('a role without a key is not served and no key text leaves the Worker', async () => {
  const ctx = await setup();
  ctx.signer = async () => null;
  const r = await act(ctx, { d: 'demo', role: 'late', action: 'buy', ip: 'a' });
  assert.equal(r.status, 503);
  assert.ok(!/ed25519/.test(JSON.stringify(r)));
});

const settleEvent = (epoch) => eventLog('settle', {
  epoch, cap: '5', requested: [[4, '100']], filled: [[4, '60']], total_requested: '100', total_filled: '60', paid: '9', bound: true, requests: 2, reserve: '100', supply: '1',
});

test('the tick settles a finished round once, records it with its transaction, and claims only where a request is settled', async () => {
  const views = { 'demo.t.near': {
    get_open: { epoch: '41', current_epoch: '43', requested: [], requests: 1, current_vintage: 4 },
    get_account: ({ account_id }) => (account_id === 'late.t.near'
      ? { lots: [], pending: [{ id: '1', epoch: '41', portions: [] }] }
      : account_id === 'panic.t.near' ? { lots: [], pending: [{ id: '2', epoch: '43', portions: [] }] } : null),
  }, 'launch.t.near': { get_open: { epoch: null, current_epoch: '5', requested: [], requests: 0, current_vintage: 0 }, get_account: null } };
  const ctx = await setup({ views, outcome: (n) => (n === 1 ? okWith(settleEvent('41'))() : okWith()()) });
  const report = await tick(ctx);
  assert.deepEqual(report.map((r) => [r.d, r.settled, r.claimed, r.errors]), [['launch', false, [], []], ['demo', true, ['late'], []]]);
  assert.equal(ctx.sent.length, 2);
  assert.ok(ctx.sent[0].includes('crank.t.near') && ctx.sent[0].includes('settle'));
  assert.ok(ctx.sent[1].includes('late.t.near') && ctx.sent[1].includes('claim'));
  const rounds = ctx.data.get('rounds:demo');
  assert.deepEqual([rounds.length, rounds[0].epoch, rounds[0].paid, rounds[0].hash.length > 40], [1, '41', '9', true]);
  await tick(ctx);
  assert.equal(ctx.data.get('rounds:demo').length, 1, 'the same round is not recorded twice');
});

test('a round settled by someone else is read from the contract while a demo role still has a request in it', async () => {
  const views = { 'demo.t.near': {
    get_open: { epoch: null, current_epoch: '43', requested: [], requests: 0, current_vintage: 4 },
    get_account: ({ account_id }) => (account_id === 'late.t.near' ? { lots: [], pending: [{ id: '1', epoch: '41', portions: [] }] } : null),
    get_settlement: { epoch: '41', cap: '5', requested: [[4, '100']], filled: [[4, '60']], total_requested: '100', total_filled: '60', paid: '9', price: '1', bound: true, open_requests: 1, paid_left: '9' },
  }, 'launch.t.near': { get_open: { epoch: null, current_epoch: '5', requested: [], requests: 0, current_vintage: 0 }, get_account: null } };
  const ctx = await setup({ views });
  await tick(ctx);
  const rounds = ctx.data.get('rounds:demo');
  assert.deepEqual([rounds.length, rounds[0].epoch, rounds[0].paid, rounds[0].bound, rounds[0].hash], [1, '41', '9', true, null]);
  await tick(ctx);
  assert.equal(ctx.data.get('rounds:demo').length, 1);
});

test('the tick does nothing when no round is waiting and nobody has a settled request', async () => {
  const quiet = { get_open: { epoch: null, current_epoch: '9', requested: [], requests: 0, current_vintage: 0 }, get_account: null };
  const ctx = await setup({ views: { 'demo.t.near': quiet, 'launch.t.near': quiet } });
  await tick(ctx);
  assert.equal(ctx.sent.length, 0);
});

test('one deployment failing does not stop the other and the error is reported', async () => {
  const ok = { get_open: { epoch: null, current_epoch: '9', requested: [], requests: 0, current_vintage: 0 }, get_account: null };
  const ctx = await setup({ views: { 'launch.t.near': { get_open: () => { throw new Error('rpc down'); } }, 'demo.t.near': ok } });
  const report = await tick(ctx);
  assert.deepEqual(report.map((r) => r.errors.length), [1, 0]);
});

test('the state call for the page makes seven requests to the chain and returns the logs and the fixed amounts', async () => {
  let views = 0;
  const ctx = await setup();
  ctx.rpc.view = async () => { views++; return { x: 1 }; };
  ctx.rpc.view = async (_c, method) => { views++; return method === 'get_open' ? { current_epoch: '9', epoch: null } : method === 'get_account' ? { lots: [], pending: [{ id: '1', epoch: '4', portions: [] }] } : { epoch: '4' }; };
  ctx.params = async () => ({ gate_bps: 500 });
  ctx.getLog = async () => ({ rounds: [{ epoch: '1' }], feed: [] });
  const s = await readState(ctx, 'demo');
  assert.equal(views, 7, 'pool, open and four accounts, one settlement read; the parameters are cached');
  assert.deepEqual(Object.keys(s.settlements), ['4']);
  assert.deepEqual([s.contract, s.plan.insider, s.rounds.length], ['demo.t.near', { buy_near: '3', sell_pct: 100 }, 1]);
});
