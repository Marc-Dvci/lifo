import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseAct, route } from '../src/routes.js';
import { LIMITS, RateLimiter, ipKey } from '../src/limits.js';

const req = (path, init) => new Request(`http://local${path}`, init);
const post = (body, headers = {}) => req('/api/act', { method: 'POST', body: typeof body === 'string' ? body : JSON.stringify(body), headers });

function deps() {
  const seen = { acts: [], states: [] };
  return {
    seen,
    state: async (d) => { seen.states.push(d); return { d }; },
    act: async (a) => { seen.acts.push(a); return { status: 200, body: { ok: true } }; },
  };
}

test('an action is exactly a deployment, a role and buy or sell', () => {
  assert.deepEqual(parseAct('{"d":"demo","role":"early","action":"buy"}').value, { d: 'demo', role: 'early', action: 'buy' });
  for (const bad of [
    '{"d":"demo","role":"early"}',
    '{"d":"demo","role":"early","action":"buy","amount":"1"}',
    '{"d":"main","role":"early","action":"buy"}',
    '{"d":"demo","role":"whale","action":"buy"}',
    '{"d":"demo","role":"early","action":"mint"}',
    '[]', 'null', '42', 'not json',
  ]) assert.ok(parseAct(bad).error, bad);
});

test('the state route reads one deployment and defaults to demo speed', async () => {
  const d = deps();
  const r = await route(req('/api/state?d=launch'), d);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'public, max-age=3');
  await route(req('/api/state'), d);
  assert.deepEqual(d.seen.states, ['launch', 'demo']);
  assert.equal((await route(req('/api/state?d=other'), d)).status, 400);
  assert.equal((await route(req('/api/state', { method: 'POST' }), d)).status, 405);
});

test('a chain failure while reading the state is a 503 that names no detail', async () => {
  const r = await route(req('/api/state?d=demo'), { state: async () => { throw new Error('rpc key abc'); } });
  assert.equal(r.status, 503);
  assert.ok(!(await r.text()).includes('abc'));
});

test('the act route refuses anything but a valid POST and never reaches the chain code', async () => {
  const d = deps();
  assert.equal((await route(req('/api/act'), d)).status, 405);
  assert.equal((await route(post({ d: 'demo', role: 'early', action: 'send' }), d)).status, 400);
  assert.equal((await route(post({ d: 'demo', role: 'early', action: 'buy', to: 'x' }), d)).status, 400);
  assert.equal((await route(post('x'.repeat(300)), d)).status, 413);
  assert.equal((await route(req('/api/other'), d)).status, 404);
  assert.equal(d.seen.acts.length, 0);
});

test('a valid act passes the visitor address to the handler and is never cached; a refusal carries retry-after', async () => {
  const d = deps();
  const r = await route(post({ d: 'demo', role: 'late', action: 'sell' }, { 'cf-connecting-ip': '203.0.113.9' }), d);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.deepEqual(d.seen.acts, [{ d: 'demo', role: 'late', action: 'sell', ip: '203.0.113.9' }]);
  const limited = await route(post({ d: 'demo', role: 'late', action: 'sell' }), { act: async () => ({ status: 429, body: { error: 'wait', retry_after: 42 } }) });
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('retry-after'), '42');
});

test('a visitor is limited per window and the window frees one slot at a time', () => {
  const rl = new RateLimiter({ perIp: { max: 3, windowMs: 1000 }, global: { max: 100, windowMs: 1000 } });
  for (let i = 0; i < 3; i++) assert.ok(rl.take('a', 100 * i).ok);
  const no = rl.take('a', 500);
  assert.deepEqual([no.ok, no.scope, no.retryAfter], [false, 'visitor', 1]);
  assert.ok(rl.take('b', 500).ok);
  assert.ok(rl.take('a', 1000).ok);
  assert.ok(!rl.take('a', 1050).ok);
});

test('the whole demo is limited even when every visitor is new', () => {
  const rl = new RateLimiter({ perIp: { max: 5, windowMs: 1000 }, global: { max: 4, windowMs: 10_000 } });
  for (let i = 0; i < 4; i++) assert.ok(rl.take(`ip${i}`, i).ok);
  const no = rl.take('ip9', 5);
  assert.equal(no.scope, 'demo');
  assert.ok(no.retryAfter >= 9);
  assert.ok(rl.take('ip9', 10_000).ok);
});

test('a refused action is not counted and the state stays bounded', () => {
  const rl = new RateLimiter({ perIp: { max: 1, windowMs: 1000 }, global: { max: 10_000, windowMs: 1000 } });
  rl.take('a', 0);
  for (let i = 0; i < 20; i++) rl.take('a', 1 + i);
  assert.equal(rl.state.global.length, 1);
  for (let i = 0; i < 2100; i++) rl.take(`x${i}`, 2000 + i);
  assert.ok(Object.keys(rl.state.ips).length <= 2000);
});

test('the default limits and the stored visitor key do not expose the address', async () => {
  assert.deepEqual([LIMITS.perIp.max, LIMITS.global.max], [8, 120]);
  const k = await ipKey('203.0.113.9');
  assert.match(k, /^[0-9a-f]{16}$/);
  assert.notEqual(k, await ipKey('203.0.113.10'));
});
