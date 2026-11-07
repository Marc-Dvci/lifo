import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildStack, describeAction, describeRound, fmtClock, fmtNear, fmtTokens, gateMeter, listVintages, placeInLine,
  plateColor, roundClock, sellPayout,
} from '../lib.js';

const T = 10n ** 18n;
const N = 10n ** 24n;
const MIN = 60_000;
const params = {
  gate_bps: 500, sell_fee_bps: 50, epoch_length: String(MIN * 1_000_000), vintage_length: String(3 * MIN * 1_000_000),
  launch_time: String(1_000_000n * 1_000_000_000n),
};
const launch = 1_000_000_000; // ms
const lot = (v, tokens) => [v, String(BigInt(tokens) * T)];
const acct = (lots, pending = []) => ({ id: 'x', view: { lots, pending, paid_out: '0' } });

test('numbers are shown in the units a reader expects', () => {
  assert.equal(fmtNear(String(41n * N / 10n)), '4.10');
  assert.equal(fmtNear('0'), '0');
  assert.equal(fmtNear(String(N / 100n)), '0.010');
  assert.equal(fmtTokens(String(31_400_000n * T)), '31.4 M');
  assert.equal(fmtTokens(String(1_204n * T)), '1.2 K');
  assert.equal(fmtTokens(String(T / 2n)), '<1');
  assert.equal(fmtClock(42_300), '0:43');
  assert.equal(fmtClock(-5), '0:00');
});

test('vintages are listed newest first, with runs of three or more as a range', () => {
  assert.equal(listVintages([8, 9]), '9 and 8');
  assert.equal(listVintages([1, 2, 3, 4, 5, 6]), '6 to 1');
  assert.equal(listVintages([9, 7, 3, 2, 1]), '9, 7 and 3 to 1');
  assert.equal(listVintages([5]), '5');
});

const settle = (over = {}) => ({
  epoch: '1412', paid: String(41n * N / 10n), bound: true, requests: 5,
  requested: [[9, String(10n * T)], [8, String(10n * T)], [7, String(10n * T)], [3, String(5n * T)], [2, String(5n * T)], [1, String(5n * T)]],
  filled: [[9, String(10n * T)], [8, String(10n * T)], [7, String(39n * T / 10n)]],
  ...over,
});

test('a round where the gate binds says who was filled, who was partly filled and who waits', () => {
  assert.equal(
    describeRound(settle()),
    'Round 1412: 4.10 NEAR paid. The gate bound: plates 9 and 8 filled, plate 7 at 39 %, plates 3 to 1 wait.',
  );
});

test('a round where the gate does not bind says that everyone was paid in full', () => {
  const ev = settle({ bound: false, requests: 3 });
  assert.equal(describeRound(ev), 'Round 1412: 4.10 NEAR paid. The gate did not bind: all 3 sell requests were filled in full.');
  assert.match(describeRound(settle({ bound: false, requests: 1 })), /the sell request was filled in full/);
});

test('a partly filled plate never reads 0 % or 100 %', () => {
  const tiny = settle({ requested: [[4, String(1000n * T)]], filled: [[4, String(T / 10n)]] });
  assert.match(describeRound(tiny), /plate 4 at 1 %/);
  const almost = settle({ requested: [[4, String(1000n * T)]], filled: [[4, String(1000n * T - 1n)]] });
  assert.match(describeRound(almost), /plate 4 at 99 %/);
  const none = settle({ requested: [[4, String(T)]], filled: [] });
  assert.match(describeRound(none), /plate 4 waits\./);
});

test('actions are described, including a refusal by the contract', () => {
  assert.equal(describeAction({ role: 'early', action: 'buy', ok: true, tokens: String(70_100_000n * T), deposit: String(2n * N), vintage: 0 }), 'early buyer bought 70.1 M LIFO for 2.00 NEAR, plate 0');
  assert.equal(describeAction({ role: 'panic', action: 'sell', ok: true, amount: String(1_000_000n * T) }), 'panic seller asked to sell 1.00 M LIFO');
  assert.equal(describeAction({ role: 'late', action: 'buy', ok: false, error: 'Smart contract panicked: max supply reached' }), 'late buyer tried to buy and the contract refused: max supply reached');
  assert.equal(describeAction({ role: 'crank', action: 'settle', ok: true }), 'a finished round was settled');
});

const open = (requested = [], extra = {}) => ({ epoch: requested.length ? '3' : null, requested, requests: requested.length ? 1 : 0, current_epoch: '3', current_vintage: 2, ...extra });

test('the stack lists the newest plate first and sizes a plate by what is held and queued', () => {
  const accounts = {
    early: acct([lot(0, 100), lot(1, 50)]),
    late: acct([lot(2, 40)]),
    insider: acct([lot(1, 50)]),
    panic: { id: 'p', view: null },
  };
  const s = buildStack({ params, open: open(), accounts, now: launch + 7 * MIN });
  assert.deepEqual(s.map((p) => p.vintage), [2, 1, 0]);
  assert.deepEqual(s.map((p) => p.total), [40n * T, 100n * T, 100n * T]);
  assert.equal(s[0].open, true);
  assert.equal(s[0].age, 'open now');
  assert.equal(s[2].age, '7 min old');
  assert.equal(s[1].width, 1);
  assert.ok(s[0].width >= 0.22 && s[0].width < 1);
  assert.deepEqual(Object.keys(s[1].mine).sort(), ['early', 'insider']);
});

test('tokens queued in the open round stay on their plate, hatched, and a role sees its own queue', () => {
  const accounts = {
    early: acct([lot(0, 60)]),
    insider: acct([lot(1, 10)], [{ id: '1', epoch: '3', portions: [[1, String(30n * T), '0']] }]),
    late: { id: 'l', view: null }, panic: { id: 'p', view: null },
  };
  const s = buildStack({ params, open: open([[1, String(30n * T)]]), accounts, now: launch + 4 * MIN });
  const p1 = s.find((p) => p.vintage === 1);
  assert.equal(p1.held, 10n * T);
  assert.equal(p1.queued, 30n * T);
  assert.equal(p1.queuedShare, 0.75);
  assert.deepEqual(p1.mine.insider, { held: 10n * T, queued: 30n * T });
});

test('a plate with nothing left disappears from the stack', () => {
  const s = buildStack({ params, open: open(), accounts: { early: acct([lot(0, 0)]) }, now: launch });
  assert.deepEqual(s, []);
});

test('plates fade with age: the newest is the brightest', () => {
  const [a, b, c] = [0, 1, 2].map((i) => plateColor(i, 3).fill);
  assert.equal(a, 'hsl(345 100% 56%)');
  assert.equal(c, 'hsl(225 32% 74%)');
  assert.notEqual(a, b);
  assert.equal(plateColor(0, 1).fill, a);
  assert.equal(plateColor(0, 3).ink, '#fff');
  assert.equal(plateColor(2, 3).ink, '#1b1b2f');
});

test('a role sees its newest plate, how far from the top it is, and what is paid before it', () => {
  const accounts = { early: acct([lot(0, 100)]), late: acct([lot(2, 40)]), insider: acct([lot(1, 50)]), panic: { id: 'p', view: null } };
  const s = buildStack({ params, open: open(), accounts, now: launch + 7 * MIN });
  assert.match(placeInLine(s, 'late').text, /your newest plate is the top plate \(plate 2\)/);
  const early = placeInLine(s, 'early');
  assert.equal(early.aheadTokens, 90n * T);
  assert.equal(early.text, 'Early buyer: your newest plate is plate 0; 2 plates sit above it. In a crash, 90 LIFO on newer plates is paid before it.');
  assert.equal(placeInLine(s, 'panic'), null);
});

const pool = { x: String(40n * N), y: String(800_000_000n * T), reserve: String(10n * N) };

test('the gate limit is the gate share of the reserve and demand is what the curve would pay, fee included', () => {
  const quiet = gateMeter({ params, pool, open: open() });
  assert.equal(quiet.cap, N / 2n);
  assert.deepEqual([quiet.ask, quiet.binds, quiet.requests], [0n, false, 0]);
  const big = gateMeter({ params, pool, open: open([[2, String(100_000_000n * T)]]) });
  assert.equal(big.binds, true);
  assert.equal(big.ask, sellPayout(pool, 100_000_000n * T, 50));
  const gross = 40n * N - (40n * N * 800_000_000n * T + (900_000_000n * T - 1n)) / (900_000_000n * T);
  assert.equal(big.ask, gross - (gross * 50n + 9999n) / 10000n);
  const small = gateMeter({ params, pool, open: open([[2, String(1_000_000n * T)]]) });
  assert.equal(small.binds, false);
  assert.ok(small.askShare > 0 && small.askShare < 1 && small.capShare === 1);
});

test('the round clock counts down to the end of the current round', () => {
  const c = roundClock(params, launch + 5 * MIN + 15_000);
  assert.deepEqual([c.epoch, c.msLeft, c.frac], [5, 45_000, 0.25]);
  assert.equal(roundClock(params, launch - 10).epoch, 0);
});
