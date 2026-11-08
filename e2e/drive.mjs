// Drives the page in a headless Chromium at 1440 and 360 px while a crash runs, and saves screenshots to
// .guest/screens/ (local, not committed). It also calls the Worker's scheduled handler every 20 s, because a local
// `wrangler dev` does not fire cron by itself.
//
//   node e2e/drive.mjs [--url http://127.0.0.1:8797] [--d demo] [--max-minutes 20]
//
// Screens: before (the first crash step not yet taken), building (three plates or more), during (sell requests are
// queued in the open round), after (a round settled with the gate binding). It stops after "after" (plus a second
// "after" 30 s later) or at the time limit, and prints what it saw as JSON.
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const url = arg('url', 'http://127.0.0.1:8797');
const d = arg('d', 'demo');
const only = arg('only', '');
const limit = Number(arg('max-minutes', '20')) * 60_000;
const dir = new URL('../.guest/screens/', import.meta.url);
mkdirSync(fileURLToPath(dir), { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// playwright-core may expect a newer browser build than the one installed: pass the installed headless shell with --exe.
const exe = arg('exe', undefined);
const browser = await chromium.launch({ headless: true, executablePath: exe });
const sizes = [[1440, 900, 1], [360, 780, 2]];
const pages = [];
const problems = [];
for (const [w, h, scale] of sizes) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`${w}px console: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`${w}px page error: ${e.message}`));
  await page.goto(`${url}/${d === 'launch' ? '#launch' : ''}`);
  pages.push({ w, page });
}

const facts = [];
async function shoot(name) {
  for (const { w, page } of pages) {
    await page.screenshot({ path: fileURLToPath(new URL(`${name}-${w}.png`, dir)), fullPage: true });
    const f = await page.evaluate(() => ({
      plates: [...document.querySelectorAll('#stack .plate')].map((li) => `${li.querySelector('.meta b').textContent} ${li.querySelector('.amt').textContent}${li.querySelector('.queued').style.width !== '0%' && li.querySelector('.queued').style.width ? ` (queued ${li.querySelector('.queued').style.width})` : ''}`),
      clock: document.getElementById('clock-time').textContent,
      gate: document.getElementById('gate-text').textContent,
      pool: document.getElementById('pool-line').textContent,
      rounds: [...document.querySelectorAll('#rounds li')].map((li) => li.textContent),
      overflowX: document.documentElement.scrollWidth - window.innerWidth,
    }));
    facts.push({ screen: `${name}-${w}`, ...f });
  }
  console.log(`shot ${name}`);
}

let lastTick = 0;
const first = await (await fetch(`${url}/api/state?d=${d}`)).json();
const baseline = Math.max(0, ...first.rounds.map((r) => Number(r.epoch)));
const done = { before: false, building: false, during: false, after: false };
const t0 = Date.now();
await sleep(2500);
await shoot(only || 'before');
done.before = true;
while (!only && Date.now() - t0 < limit) {
  if (Date.now() - lastTick > 20_000) {
    lastTick = Date.now();
    await fetch(`${url}/cdn-cgi/handler/scheduled`).catch(() => {});
  }
  const s = await (await fetch(`${url}/api/state?d=${d}`)).json().catch(() => null);
  if (s) {
    const held = new Set(Object.values(s.accounts).flatMap((a) => (a.view?.lots ?? []).map(([v]) => v)));
    if (!done.building && held.size >= 3) { await sleep(6000); await shoot('building'); done.building = true; }
    if (!done.during && s.open.requests > 0) { await sleep(6000); await shoot('during'); done.during = true; }
    if (done.during && !done.after && s.rounds.some((r) => r.bound && Number(r.epoch) > Number(s.open.requests ? s.open.epoch : baseline))) { await sleep(8000); await shoot('after'); done.after = true; lastTick = 0; }
    if (done.after && s.rounds.length >= 2) { await sleep(8000); await shoot('after-second-round'); break; }
  }
  await sleep(3000);
}
// Act through the page once, as a visitor would: choose a role and press buy.
const p = pages[0].page;
if (only) { await browser.close(); console.log(JSON.stringify({ shot: only, facts: facts.map((f) => [f.screen, f.plates, f.rounds[0], f.overflowX]) })); process.exit(0); }
await p.click('#roles button[data-role="late"]');
const before = await p.locator('#act-msg').textContent();
await p.click('#buy');
await p.waitForFunction(() => /Done|did not|cannot|used your|contract said/i.test(document.getElementById('act-msg').textContent), null, { timeout: 60_000 }).catch(() => {});
const clicked = { before, after: await p.locator('#act-msg').textContent() };
await shoot('after-click');
await browser.close();
const out = { done, problems, clicked, facts };
writeFileSync(fileURLToPath(new URL('drive.json', dir)), JSON.stringify(out, null, 2));
console.log(JSON.stringify({ done, problems, clicked, overflow: facts.map((f) => [f.screen, f.overflowX]) }, null, 1));
