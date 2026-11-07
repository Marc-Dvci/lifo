// Initialisation, deposit and source-metadata checks of the LIFO contract on a local NEAR sandbox (not testnet).
// Writes results/e2e-checks.md and results/e2e-checks.json.
//
//   bash e2e/sandbox.sh start          (see the script for SANDBOX_DIR and GLIBC_DIR)
//   cd e2e && npm install && node checks.mjs
//
// Checks: a second `new` is refused; `new` from another account is refused; a deposit of 1 yoctoNEAR on `settle`,
// `claim` and `ft_resolve_transfer` is refused, and on a view it leaves the reserve unchanged;
// `contract_source_metadata` returns the repository link and the standards.
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { provider, account, call, view, createSubAccount, nearToYocto } from './near.js';

const RPC = process.env.RPC || 'http://127.0.0.1:3030';
const KEY_FILE = process.env.ROOT_KEY_FILE || '/tmp/lifo-sandbox/validator_key.json';
const WASM = new URL('../target/near/lifo/lifo.wasm', import.meta.url);
const OUT = new URL('../results/', import.meta.url);
const NEAR = 10n ** 24n;
const TOKEN = 10n ** 18n;
const LINK = 'https://github.com/Marc-Dvci/lifo';
const STANDARDS = ['nep141', 'nep145', 'nep148', 'nep297', 'nep330'];

// Every public method without #[payable]: first the three that change state, then the views.
const NOT_PAYABLE = [
  ['settle', {}],
  ['claim', {}],
  ['ft_resolve_transfer', { sender_id: 'x.test.near', receiver_id: 'y.test.near', amount: '1', oldest: 0 }],
  ['ft_total_supply', {}],
  ['ft_balance_of', { account_id: 'x.test.near' }],
  ['ft_metadata', {}],
  ['storage_balance_bounds', {}],
  ['storage_balance_of', { account_id: 'x.test.near' }],
  ['get_params', {}],
  ['get_request_storage', {}],
  ['get_pool', {}],
  ['get_open', {}],
  ['get_settlement', { epoch: '0' }],
  ['get_account', { account_id: 'x.test.near' }],
  ['quote_buy', { deposit: '1' }],
  ['contract_source_metadata', {}],
];

const keyText = process.platform === 'win32'
  ? execFileSync('wsl', ['-d', process.env.WSL_DISTRO || 'Ubuntu-22.04', '--', 'cat', KEY_FILE]).toString()
  : readFileSync(KEY_FILE, 'utf8');
const key = JSON.parse(keyText);
const rpc = provider(RPC);
const root = account(key.account_id, rpc, key.secret_key);
const git = (...a) => execFileSync('git', a, { cwd: new URL('..', import.meta.url) }).toString().trim();
const why = (res) => JSON.stringify(res.fails[0]?.failure ?? null);

const results = [];
function record(check, pass, detail) {
  results.push({ check, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${check}  ${detail}`);
}

const wasm = readFileSync(WASM);
const prefix = `c${Date.now().toString(36)}`;
const { id: cid, secretKey } = await createSubAccount(root, prefix, '10');
const { id: otherId, secretKey: otherKey } = await createSubAccount(root, `${prefix}x`, '5');
const owner = account(cid, rpc, secretKey);
const other = account(otherId, rpc, otherKey);
const block = await rpc.viewBlock({ finality: 'final' });
const params = {
  virtual_near: String(30n * NEAR),
  virtual_tokens: String(1_073_000_000n * TOKEN),
  max_supply: String((1_073_000_000n * TOKEN * 8n) / 10n),
  gate_bps: 500,
  sell_fee_bps: 50,
  epoch_length: String(600_000_000_000n),
  vintage_length: String(86_400_000_000_000n),
  launch_time: String(BigInt(block.header.timestamp_nanosec)),
  max_lots: 16,
};

// Deploy without initialising, so that the first `new` can come from another account.
const { actions } = await import('near-api-js');
await owner.signAndSendTransaction({ receiverId: cid, actions: [actions.deployContract(wasm)], waitUntil: 'FINAL', throwOnFailure: true });

const fromOther = await call(other, cid, 'new', { params });
record('new from another account is refused', fromOther.fails.length > 0 && /private/i.test(why(fromOther)), why(fromOther).slice(0, 160));

const first = await call(owner, cid, 'new', { params });
record('new from the contract account succeeds once', first.fails.length === 0, first.fails.length ? why(first).slice(0, 160) : 'ok');

const second = await call(owner, cid, 'new', { params });
record('a second new is refused', second.fails.length > 0 && /already|initiali/i.test(why(second)), why(second).slice(0, 160));

const bought = await call(other, cid, 'buy', {}, { deposit: nearToYocto('1') });
record('a buy puts NEAR in the reserve', bought.fails.length === 0, bought.fails.length ? why(bought).slice(0, 160) : `tokens ${bought.value}`);

for (const [method, args] of NOT_PAYABLE.slice(0, 3)) {
  const res = await call(other, cid, method, args, { deposit: 1n });
  const refused = res.fails.length > 0 && /deposit|private/i.test(why(res));
  record(`a deposit on ${method} is refused`, refused, why(res).slice(0, 160));
}

// near-sdk adds no deposit check to view methods (`&self`): called in a transaction they keep the yoctoNEAR in the
// contract account. What matters is that the pool's reserve, which the contract keeps itself, does not change.
for (const [method, args] of NOT_PAYABLE.slice(3)) {
  const before = (await view(rpc, cid, 'get_pool')).reserve;
  const res = await call(other, cid, method, args, { deposit: 1n });
  const after = (await view(rpc, cid, 'get_pool')).reserve;
  record(
    `a deposit on the view ${method} is accepted and leaves the reserve unchanged`,
    res.fails.length === 0 && before === after,
    res.fails.length ? why(res).slice(0, 160) : `accepted; reserve ${before} -> ${after}`,
  );
}

const meta = await view(rpc, cid, 'contract_source_metadata');
const names = (meta.standards || []).map((s) => s.standard);
record(
  'contract_source_metadata has the link and the standards',
  meta.link === LINK && STANDARDS.every((s) => names.includes(s)),
  JSON.stringify(meta),
);

const failed = results.filter((r) => !r.pass).length;
const header = {
  command: 'cd e2e && node checks.mjs (local NEAR sandbox 2.14.0, not testnet)',
  commit: git('rev-parse', 'HEAD'),
  wasm_sha256: createHash('sha256').update(wasm).digest('hex'),
  wasm_bytes: wasm.length,
  contract: cid,
  passed: results.length - failed,
  failed,
};
writeFileSync(new URL('e2e-checks.json', OUT), JSON.stringify({ ...header, results }, null, 2) + '\n');
const rows = results.map((r) => `| ${r.check} | ${r.pass ? 'pass' : 'FAIL'} | \`${r.detail.replaceAll('|', '\\|').slice(0, 120)}\` |`);
writeFileSync(
  new URL('e2e-checks.md', OUT),
  [
    '# End-to-end checks on a local sandbox',
    '',
    `Command: \`${header.command}\`. Commit \`${header.commit}\`. Wasm ${header.wasm_bytes} bytes, SHA-256 \`${header.wasm_sha256}\`.`,
    `${header.passed} passed, ${header.failed} failed.`,
    '',
    '| check | result | what the chain returned |',
    '|---|---|---|',
    ...rows,
    '',
  ].join('\n'),
);
console.log(`${header.passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
