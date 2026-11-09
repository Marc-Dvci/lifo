// Accessibility check of the live page with axe-core, at 1440 px and 390 px, on both deployments.
//
//   cd e2e && npm install && node axe.mjs [--url https://lifo-amt.pages.dev] [--exe <chromium headless shell>]
//
// It prints every violation and exits 1 if there is any.
import { chromium } from 'playwright-core';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const url = arg('url', 'https://lifo-amt.pages.dev').replace(/\/$/, '');
const AXE = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js';

const browser = await chromium.launch({ headless: true, executablePath: arg('exe', undefined) });
let total = 0;
for (const [w, h, scale] of [[1440, 900, 1], [390, 844, 2]]) {
  for (const hash of ['', '#launch']) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
    const page = await ctx.newPage();
    await page.goto(`${url}/${hash}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#stack li');
    await page.addScriptTag({ url: AXE });
    const violations = await page.evaluate(async () => (await window.axe.run(document)).violations
      .map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.map((n) => n.target.join(' ')) })));
    console.log(`${w}px ${hash || '#demo'}: ${violations.length} violation(s)`);
    for (const v of violations) console.log(`  ${v.impact} ${v.id}: ${v.nodes.join(', ')}`);
    total += violations.length;
    await ctx.close();
  }
}
await browser.close();
process.exit(total ? 1 : 0);
