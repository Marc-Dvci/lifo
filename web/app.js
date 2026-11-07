// The page: reads /api/state, draws the stack, and sends the demo roles' actions to /api/act.
// All wording and arithmetic live in lib.js (tested); this file only touches the DOM.
import {
  ROLES, ROLE_LABEL, ROLE_TEXT, buildStack, describeAction, describeRound, explorerTxUrl, fmtClock, fmtDuration, fmtNear,
  fmtPricePerMillion, fmtTokens, gateMeter, placeInLine, roundClock, epochMs, vintageMs,
} from './lib.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 5000;
const IDLE_STOP_MS = 15 * 60 * 1000;

let d = location.hash === '#launch' ? 'launch' : 'demo';
let role = 'late';
let state = null;
let offset = 0;
let failures = 0;
let busy = false;
let lastTouch = Date.now();
const rows = new Map();
const seen = { rounds: null, feed: null };

const now = () => Date.now() + offset;
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const link = (href, text) => {
  const a = el('a', 'tx', text);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener';
  return a;
};

// --- loading ------------------------------------------------------------------------------------------------------

async function load() {
  const asked = d;
  try {
    const res = await fetch(`/api/state?d=${asked}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(String(res.status));
    const next = await res.json();
    if (asked !== d) return;
    state = next;
    offset = next.now - Date.now();
    failures = 0;
    render();
  } catch {
    failures++;
  }
  $('server-error').hidden = failures < 2;
}

function poll() {
  if (document.hidden || Date.now() - lastTouch > IDLE_STOP_MS) return;
  load();
}
setInterval(poll, POLL_MS);
setInterval(() => state && renderClock(), 250);
for (const ev of ['pointerdown', 'keydown', 'visibilitychange']) {
  addEventListener(ev, () => {
    const wasIdle = Date.now() - lastTouch > IDLE_STOP_MS;
    lastTouch = Date.now();
    if (wasIdle) load();
  });
}

// --- drawing ------------------------------------------------------------------------------------------------------

function render() {
  const { params } = state;
  $('mode').replaceChildren();
  const rule = `rounds of ${fmtDuration(epochMs(params))}, plates of ${fmtDuration(vintageMs(params))}`;
  if (d === 'demo') {
    const b = el('b', '', 'Demo speed');
    $('mode').append(b, ` is the same contract code on a fast clock (${rule}), so you see plates and rounds within minutes. It is not the launch setting.`);
  } else {
    $('mode').append(el('b', '', 'Launch rules'), ` are the real settings (${rule}). Things move slowly here.`);
  }
  for (const b of document.querySelectorAll('.switch button')) b.setAttribute('aria-pressed', String(b.dataset.d === d));
  const c = $('link-contract');
  c.hidden = !state.explorer.account;
  c.href = `${state.explorer.account}${state.contract}`;
  renderStack();
  renderClock();
  renderGate();
  renderPool();
  renderYou();
  renderLogs();
}

function initials(plate) {
  return ROLES.filter((r) => plate.mine[r]).map((r) => r[0].toUpperCase());
}

function renderStack() {
  const stack = buildStack({ params: state.params, open: state.open, accounts: state.accounts, now: now() });
  const ol = $('stack');
  const keep = new Set(stack.map((p) => p.vintage));
  for (const [v, li] of rows) {
    if (keep.has(v)) continue;
    rows.delete(v);
    li.classList.add('leaving');
    setTimeout(() => li.remove(), 650);
  }
  ol.querySelector('.empty')?.remove();
  stack.forEach((p, i) => {
    let li = rows.get(p.vintage);
    if (!li) {
      li = el('li', 'plate');
      const meta = el('div', 'meta');
      meta.append(el('b'), el('small'));
      const lane = el('div', 'lane');
      const disc = el('div', 'disc');
      disc.append(el('span', 'amt'), el('span', 'who'), el('span', 'queued'));
      lane.append(disc);
      li.append(meta, lane);
      rows.set(p.vintage, li);
    }
    const disc = li.querySelector('.disc');
    li.querySelector('.meta b').textContent = `plate ${p.vintage}`;
    li.querySelector('.meta small').textContent = p.age;
    disc.style.width = `${Math.round(p.width * 100)}%`;
    disc.style.backgroundColor = p.color.fill;
    disc.style.color = p.color.ink;
    li.querySelector('.amt').textContent = fmtTokens(p.total);
    const who = li.querySelector('.who');
    who.replaceChildren(...initials(p).map((t) => el('i', '', t)));
    const q = li.querySelector('.queued');
    q.style.width = `${Math.round(p.queuedShare * 100)}%`;
    q.title = p.queued > 0n ? `${fmtTokens(p.queued)} LIFO waiting to be sold in the open round` : '';
    li.classList.toggle('mine', Boolean(p.mine[role]));
    li.title = `${fmtTokens(p.held)} LIFO held${p.queued > 0n ? `, ${fmtTokens(p.queued)} queued to sell` : ''}`;
    if (ol.children[i] !== li) ol.insertBefore(li, ol.children[i] ?? null);
  });
  if (!stack.length) ol.append(el('li', 'empty', 'No plates yet. Buy as the early buyer to put the first one down.'));
  const shown = stack.reduce((n, p) => n + p.held, 0n);
  const others = BigInt(state.pool.supply) - BigInt(state.pool.locked) - shown;
  const small = others * 100n < BigInt(state.pool.supply) || others <= 0n;
  $('stack-note').textContent = small
    ? 'Plates show what the four demo accounts hold. A plate is everything bought in the same stretch of time. Hatched part: waiting to be sold in this round.'
    : `Plates show what the four demo accounts hold. Other accounts hold ${fmtTokens(others)} LIFO that is not drawn. Hatched part: waiting to be sold in this round.`;
  state.stack = stack;
}

function renderClock() {
  const c = roundClock(state.params, now());
  $('clock-time').textContent = fmtClock(c.msLeft);
  $('clock-bar').style.width = `${Math.round(c.frac * 100)}%`;
  $('clock-text').textContent = `Round ${c.epoch} is open. A sell request waits for the round to end, then the gate decides who is paid.`;
}

function renderGate() {
  const g = gateMeter({ params: state.params, pool: state.pool, open: state.open });
  const pct = state.params.gate_bps / 100;
  // The track spans the larger of the limit and the demand; the limit is marked on it.
  const scale = Number(g.cap > g.ask ? g.cap : g.ask) || 1;
  const capPos = (Number(g.cap) / scale) * 100;
  const inside = g.ask < g.cap ? g.ask : g.cap;
  $('gate-cap').style.left = `${capPos}%`;
  $('gate-in').style.width = `${(Number(inside) / scale) * 100}%`;
  $('gate-over').style.left = `${capPos}%`;
  $('gate-over').style.width = g.binds ? `${((Number(g.ask) - Number(g.cap)) / scale) * 100}%` : '0%';  const t = $('gate-text');
  t.replaceChildren();
  const limit = `${fmtNear(g.cap)} NEAR (${pct} % of the ${fmtNear(state.pool.reserve)} NEAR reserve)`;
  if (g.requests === 0) {
    t.append(`No one is selling. At most ${limit} can leave in one round.`);
  } else if (g.binds) {
    t.append(el('span', 'bind', 'The gate binds. '), `${fmtNear(g.ask)} NEAR is asked against a limit of ${fmtNear(g.cap)} NEAR. The newest plates are paid first when the round ends; the oldest wait.`);
  } else {
    t.append(`${fmtNear(g.ask)} NEAR is asked, under the limit of ${limit}. Everyone is paid in full.`);
  }
}

function renderPool() {
  const p = state.pool;
  const line = $('pool-line');
  line.replaceChildren();
  const part = (label, value) => {
    const s = el('span');
    s.append(`${label} `, el('b', '', value));
    return s;
  };
  line.append(
    part('Price', `${fmtPricePerMillion(p.spot_price)} NEAR per million LIFO`),
    part('Reserve', `${fmtNear(p.reserve)} NEAR`),
    part('Supply', `${fmtTokens(p.supply)} LIFO`),
  );
}

function renderYou() {
  const box = $('roles');
  if (!box.children.length) {
    for (const r of ROLES) {
      const b = el('button');
      b.type = 'button';
      b.dataset.role = r;
      b.append(el('i', '', r[0].toUpperCase()), ROLE_LABEL[r]);
      b.addEventListener('click', () => {
        role = r;
        $('act-msg').textContent = '';
        if (state) { renderStack(); renderYou(); }
      });
      box.append(b);
    }
  }
  for (const b of box.children) b.setAttribute('aria-pressed', String(b.dataset.role === role));
  const plan = state.plan[role];
  const held = (state.accounts[role].view?.lots ?? []).reduce((n, [, a]) => n + BigInt(a), 0n);
  $('role-text').textContent = ROLE_TEXT[role];
  const place = placeInLine(state.stack, role);
  $('place').textContent = place ? place.text : `${ROLE_LABEL[role]} holds nothing here yet. Buy to get a place in line.`;
  const buy = $('buy');
  const sell = $('sell');
  buy.replaceChildren(`Buy with ${plan.buy_near} NEAR`, el('small', '', `as ${ROLE_LABEL[role].toLowerCase()}`));
  sell.replaceChildren(`Sell ${plan.sell_pct} % of its plates`, el('small', '', held > 0n ? `${fmtTokens(held)} LIFO held` : 'nothing held yet'));
  buy.disabled = busy;
  sell.disabled = busy || held === 0n;
}

function renderLogs() {
  const rounds = $('rounds');
  const feed = $('feed');
  const fill = (ul, items, key, make, kind) => {
    const first = seen[kind] === null;
    ul.replaceChildren();
    if (!items.length) ul.append(el('li', 'none', kind === 'rounds' ? 'No round has settled yet.' : 'Nothing has happened yet.'));
    for (const it of items.slice(0, 8)) {
      const li = el('li', '', make(it));
      const url = explorerTxUrl(state.explorer, it.hash);
      if (url) li.append(link(url, 'transaction'));
      else if (it.hash) li.append(el('span', 'tx', `tx ${it.hash.slice(0, 8)}…`));
      if (!first && !seen[kind].has(key(it))) li.classList.add('new');
      ul.append(li);
    }
    seen[kind] = new Set(items.map(key));
  };
  fill(rounds, state.rounds, (r) => r.epoch, describeRound, 'rounds');
  fill(feed, state.feed, (a) => a.hash, describeAction, 'feed');
}

// --- acting -------------------------------------------------------------------------------------------------------

async function act(action) {
  busy = true;
  renderYou();
  const msg = $('act-msg');
  msg.className = 'small';
  msg.textContent = 'Sending…';
  try {
    const res = await fetch('/api/act', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ d, role, action }),
    });
    const body = await res.json();
    msg.replaceChildren();
    if (res.ok && body.ok) {
      msg.append(`Done: ${ROLE_LABEL[role].toLowerCase()} ${action === 'buy' ? 'bought' : 'asked to sell'}. `);
      const url = explorerTxUrl(state.explorer, body.hash);
      msg.append(url ? link(url, 'See the transaction') : el('span', '', `Transaction ${body.hash.slice(0, 10)}…`));
    } else {
      msg.className = 'small error';
      msg.textContent = body.error ? String(body.error).replace(/^Smart contract panicked: /, 'The contract said: ') : 'That did not work.';
    }
  } catch {
    msg.className = 'small error';
    msg.textContent = 'The demo server cannot be reached.';
  }
  busy = false;
  if (state) renderYou();
  setTimeout(load, 700);
  setTimeout(load, 3500);
}

$('buy').addEventListener('click', () => act('buy'));
$('sell').addEventListener('click', () => act('sell'));
for (const b of document.querySelectorAll('.switch button')) {
  b.addEventListener('click', () => {
    if (b.dataset.d === d) return;
    d = b.dataset.d;
    location.hash = d === 'launch' ? 'launch' : '';
    state = null;
    seen.rounds = seen.feed = null;
    for (const li of rows.values()) li.remove();
    rows.clear();
    $('act-msg').textContent = '';
    load();
  });
}

load();
