import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMemo } from '../src/memo.js';

const clock = () => {
  let t = 1_000_000;
  return { now: () => t, add: (ms) => { t += ms; } };
};

test('callers within the cache time share one read, and a failed read is not cached', async () => {
  const c = clock();
  const memo = createMemo(c.now);
  let reads = 0;
  const read = async () => ++reads;
  assert.equal(await memo.cached('s', 4000, read), 1);
  c.add(3999);
  assert.equal(await memo.cached('s', 4000, read), 1);
  c.add(2);
  assert.equal(await memo.cached('s', 4000, read), 2);
  await assert.rejects(memo.cached('f', 4000, async () => { throw new Error('rpc http 429'); }));
  assert.equal(await memo.cached('f', 4000, async () => 'ok'), 'ok');
});

test('when the chain cannot be read, the last good state is served with the current time and its age, up to the limit', async () => {
  const c = clock();
  const memo = createMemo(c.now);
  const fail = async () => { throw new Error('rpc http 429'); };
  await assert.rejects(memo.lastGood('s', 120_000, fail), /429/);
  assert.deepEqual(await memo.lastGood('s', 120_000, async () => ({ now: c.now(), pool: 1 })), { now: 1_000_000, pool: 1 });
  c.add(30_000);
  assert.deepEqual(await memo.lastGood('s', 120_000, fail), { now: 1_030_000, pool: 1, stale_ms: 30_000 });
  c.add(90_001);
  await assert.rejects(memo.lastGood('s', 120_000, fail), /429/);
});
