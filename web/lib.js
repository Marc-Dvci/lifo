// Pure functions of the page: no DOM, no network. The tests in web/test run them under Node.
// Amounts arrive as strings (yoctoNEAR, or token units with 18 decimals) and are handled as BigInt.

export const TOKEN = 10n ** 18n;
export const NEAR = 10n ** 24n;
export const ROLES = ['early', 'late', 'insider', 'panic'];
export const ROLE_LABEL = { early: 'Early buyer', late: 'Late buyer', insider: 'Insider', panic: 'Panic seller' };
export const ROLE_TEXT = {
  early: 'Buys at the start and holds the oldest plates.',
  late: 'Buys after the crowd and holds newer plates.',
  insider: 'Buys a lot early and dumps it all when the price is up.',
  panic: 'Buys late and sells everything at the first scare.',
};

const big = (v) => BigInt(v ?? 0);

// --- numbers in words ---------------------------------------------------------------------------------------------

export function fmtNear(yocto) {
  const v = Number(big(yocto) / 10n ** 18n) / 1e6;
  if (v === 0) return '0';
  if (v >= 100) return v.toFixed(1);
  if (v >= 10) return v.toFixed(1);
  if (v >= 1) return v.toFixed(2);
  if (v >= 0.01) return v.toFixed(3);
  return v.toFixed(4);
}

export function fmtTokens(units) {
  const v = Number(big(units) / 10n ** 12n) / 1e6;
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)} B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e8 ? 0 : v >= 1e7 ? 1 : 2)} M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e5 ? 0 : 1)} K`;
  return v >= 1 ? v.toFixed(0) : v > 0 ? '<1' : '0';
}

/** The pool's spot price (yoctoNEAR per whole token) as NEAR per million tokens. */
export function fmtPricePerMillion(spot) {
  const v = Number(big(spot) / 10n ** 12n) / 1e12 * 1e6;
  return v >= 1 ? v.toFixed(2) : v >= 0.01 ? v.toFixed(3) : v.toFixed(4);
}

export function fmtDuration(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

export function fmtClock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** "9", "9 and 8", "9, 7 and 3 to 1": vintages, newest first, with runs of three or more as "a to b". */
export function listVintages(vintages) {
  const v = [...new Set(vintages)].sort((a, b) => b - a);
  const parts = [];
  for (let i = 0; i < v.length;) {
    let j = i;
    while (j + 1 < v.length && v[j + 1] === v[j] - 1) j++;
    if (j - i >= 2) {
      parts.push(`${v[i]} to ${v[j]}`);
    } else {
      for (let k = i; k <= j; k++) parts.push(String(v[k]));
    }
    i = j + 1;
  }
  return parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

const plates = (list) => `${list.length === 1 && !/ /.test(listVintages(list)) ? 'plate' : 'plates'} ${listVintages(list)}`;

// --- the round log ------------------------------------------------------------------------------------------------

/** One settled round in plain words, from the contract's `settle` event. */
export function describeRound(ev) {
  const asked = new Map((ev.requested || []).map(([v, a]) => [Number(v), big(a)]));
  const got = new Map((ev.filled || []).map(([v, a]) => [Number(v), big(a)]));
  const head = `Round ${ev.epoch}: ${fmtNear(ev.paid)} NEAR paid`;
  if (!ev.bound) {
    const n = Number(ev.requests);
    return `${head}. The gate did not bind: ${n === 1 ? 'the sell request was' : `all ${n} sell requests were`} filled in full.`;
  }
  const full = [];
  const part = [];
  const wait = [];
  for (const [v, r] of asked) {
    const f = got.get(v) ?? 0n;
    if (r === 0n) continue;
    if (f >= r) full.push(v);
    else if (f === 0n) wait.push(v);
    else part.push([v, f, r]);
  }
  part.sort((a, b) => b[0] - a[0]);
  const bits = [];
  if (full.length) bits.push(`${plates(full)} filled`);
  for (const [v, f, r] of part) {
    const pct = Math.min(99, Math.max(1, Number((f * 100n) / r)));
    bits.push(`plate ${v} at ${pct} %`);
  }
  if (wait.length) bits.push(`${plates(wait)} ${wait.length === 1 ? 'waits' : 'wait'}`);
  return `${head}. The gate bound: ${bits.join(', ')}.`;
}

export function describeAction(a) {
  const who = ROLE_LABEL[a.role]?.toLowerCase() ?? a.role;
  if (!a.ok) return `${who} tried to ${a.action} and the contract refused: ${String(a.error || 'failed').replace(/^Smart contract panicked: /, '')}`;
  if (a.action === 'buy') return `${who} bought ${a.tokens ? `${fmtTokens(a.tokens)} LIFO` : 'LIFO'}${a.deposit ? ` for ${fmtNear(a.deposit)} NEAR` : ''}${a.vintage !== undefined ? `, plate ${a.vintage}` : ''}`;
  if (a.action === 'sell') return `${who} asked to sell ${a.amount ? `${fmtTokens(a.amount)} LIFO` : 'tokens'}`;
  if (a.action === 'claim') return `${who} claimed its settled requests`;
  if (a.action === 'settle') return 'a finished round was settled';
  return `${who} ${a.action}`;
}

export function explorerTxUrl(explorer, hash) {
  return explorer?.tx && hash ? `${explorer.tx}${hash}` : '';
}

// --- the stack ----------------------------------------------------------------------------------------------------

/** Newest plate bright pink, older plates fade towards a pale blue-grey. `n` is the number of plates. */
export function plateColor(rank, n) {
  const t = n <= 1 ? 0 : rank / (n - 1);
  const h = Math.round(345 - 120 * t);
  const s = Math.round(100 - 68 * t);
  const l = Math.round(56 + 18 * t);
  return { fill: `hsl(${h} ${s}% ${l}%)`, ink: l < 66 ? '#fff' : '#1b1b2f' };
}

export const launchMs = (params) => Number(big(params.launch_time) / 1_000_000n);
export const epochMs = (params) => Number(big(params.epoch_length) / 1_000_000n);
export const vintageMs = (params) => Number(big(params.vintage_length) / 1_000_000n);

/**
 * The plates, newest first. A plate is a vintage. Its size is what the demo accounts hold of it plus what is
 * queued in the open round. `mine[role]` is the share of each demo account.
 */
export function buildStack({ params, open, accounts, now }) {
  const byV = new Map();
  const slot = (v) => {
    if (!byV.has(v)) byV.set(v, { vintage: v, held: 0n, queued: 0n, mine: {} });
    return byV.get(v);
  };
  const mineOf = (p, role) => (p.mine[role] ??= { held: 0n, queued: 0n });
  for (const role of ROLES) {
    const view = accounts?.[role]?.view;
    if (!view) continue;
    for (const [v, a] of view.lots) {
      const p = slot(Number(v));
      p.held += big(a);
      mineOf(p, role).held += big(a);
    }
    for (const req of view.pending) {
      if (open?.epoch == null || String(req.epoch) !== String(open.epoch)) continue;
      for (const [v, a] of req.portions) mineOf(slot(Number(v)), role).queued += big(a);
    }
  }
  for (const [v, a] of open?.requested ?? []) slot(Number(v)).queued = big(a);
  const list = [...byV.values()].filter((p) => p.held + p.queued > 0n).sort((a, b) => b.vintage - a.vintage);
  const top = list.reduce((m, p) => (p.held + p.queued > m ? p.held + p.queued : m), 0n);
  const currentV = Number(open?.current_vintage ?? -1);
  return list.map((p, i) => {
    const total = p.held + p.queued;
    const share = top === 0n ? 0 : Number((total * 1000n) / top) / 1000;
    const startedAgo = now - (launchMs(params) + p.vintage * vintageMs(params));
    return {
      ...p,
      total,
      rank: i,
      width: Math.max(0.22, share),
      queuedShare: Number((p.queued * 1000n) / total) / 1000,
      color: plateColor(i, list.length),
      open: p.vintage === currentV,
      age: p.vintage === currentV ? 'still open to buyers' : `opened ${fmtDuration(startedAgo)} ago`,
    };
  });
}

/** Where one demo role stands in the line: its plates, their place from the top, and what is paid before it. */
export function placeInLine(stack, role) {
  const mine = stack.map((p, i) => ({ p, i, amt: (p.mine[role]?.held ?? 0n) + (p.mine[role]?.queued ?? 0n) })).filter((x) => x.amt > 0n);
  if (!mine.length) return null;
  const first = mine[0];
  const ahead = stack.slice(0, first.i).reduce((n, p) => n + p.total, 0n);
  const places = mine.map((x) => ({ vintage: x.p.vintage, position: x.i + 1, amount: x.amt }));
  const text = first.i === 0
    ? `${ROLE_LABEL[role]}: your newest plate is the top plate (plate ${first.p.vintage}). In a crash it is paid first.`
    : `${ROLE_LABEL[role]}: your newest plate is plate ${first.p.vintage}; ${first.i} ${first.i === 1 ? 'plate sits' : 'plates sit'} above it. In a crash, ${fmtTokens(ahead)} LIFO on newer plates is paid before it.`;
  return { places, aheadTokens: ahead, text };
}

// --- the gate and the clock -----------------------------------------------------------------------------------------

const ceilDiv = (a, b) => (a + b - 1n) / b;

/** NEAR the pool pays for `tokens` if all were filled: the curve, then the fee rounded up (as the contract does). */
export function sellPayout(pool, tokens, feeBps) {
  const t = big(tokens);
  if (t === 0n) return 0n;
  const x = big(pool.x);
  const y = big(pool.y);
  const gross = x - ceilDiv(x * y, y + t);
  return gross - ceilDiv(gross * big(feeBps), 10000n);
}

export function gateMeter({ params, pool, open }) {
  const cap = (big(pool.reserve) * big(params.gate_bps)) / 10000n;
  const asked = (open?.requested ?? []).reduce((n, [, a]) => n + big(a), 0n);
  const ask = sellPayout(pool, asked, params.sell_fee_bps);
  const scale = (cap > ask ? cap : ask) || 1n;
  return {
    cap,
    ask,
    askedTokens: asked,
    requests: Number(open?.requests ?? 0),
    binds: ask > cap,
    capShare: Number((cap * 1000n) / scale) / 1000,
    askShare: Number((ask * 1000n) / scale) / 1000,
  };
}

export function roundClock(params, now) {
  const len = epochMs(params);
  const elapsed = Math.max(0, now - launchMs(params));
  const epoch = Math.floor(elapsed / len);
  const left = len - (elapsed % len);
  return { epoch, msLeft: left, frac: 1 - left / len, length: len };
}
