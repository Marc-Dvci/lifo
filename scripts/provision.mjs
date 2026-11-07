// Creates the accounts of the demo, deploys the contract twice and initialises both deployments.
//
//   node scripts/provision.mjs --network sandbox          (root key read from the local sandbox)
//   PARENT_ID=lifo-demo.testnet PARENT_KEY=ed25519:... node scripts/provision.mjs --network testnet
//
// Accounts, all sub-accounts of the parent: launch and demo (the two contracts), early, late, insider, panic (the
// demo roles) and crank (settles rounds). Both deployments run the same wasm with the same curve, gate and fee; they
// differ only in the clock. After `new`, the full-access key of each contract account is deleted, so that nobody can
// redeploy or move funds: the check reads the key list back. Keys of the other accounts are written to
// .guest/keys-<network>.json (never committed) and, as a secrets file for `wrangler secret bulk`, to
// .guest/secrets-<network>.json. For the sandbox the Worker's local variables go to worker/.dev.vars.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { provider, account, createSubAccount, deploy, view, actions, KeyPair } from '../e2e/near.js';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const network = arg('network', 'sandbox');
const rpcUrl = arg('rpc', network === 'sandbox' ? 'http://127.0.0.1:3030' : 'https://rpc.testnet.near.org');
const wasmPath = new URL(arg('wasm', '../target/near/lifo/lifo.wasm'), import.meta.url);
const roleNear = arg('role-near', network === 'sandbox' ? '200' : '40');
const contractNear = arg('contract-near', '5');
const crankNear = arg('crank-near', '5');
const outDir = new URL('../.guest/', import.meta.url);
const NEAR = 10n ** 24n;
const TOKEN = 10n ** 18n;
const SECOND = 1_000_000_000n;

const rpc = provider(rpcUrl);
let parentId;
let parentKey;
if (network === 'sandbox') {
  const file = process.env.ROOT_KEY_FILE || '/tmp/lifo-sandbox/validator_key.json';
  const text = process.platform === 'win32'
    ? execFileSync('wsl', ['-d', process.env.WSL_DISTRO || 'Ubuntu-22.04', '--', 'cat', file]).toString()
    : readFileSync(file, 'utf8');
  const k = JSON.parse(text);
  parentId = k.account_id;
  parentKey = k.secret_key;
} else {
  parentId = process.env.PARENT_ID;
  parentKey = process.env.PARENT_KEY;
  if (!parentId || !parentKey) throw new Error('set PARENT_ID and PARENT_KEY (a funded account that will own the demo accounts)');
}
const parent = account(parentId, rpc, parentKey);

const curve = {
  virtual_near: String(30n * NEAR),
  virtual_tokens: String(1_073_000_000n * TOKEN),
  max_supply: String((1_073_000_000n * TOKEN * 8n) / 10n),
  gate_bps: 500,
  sell_fee_bps: 50,
  max_lots: 16,
};
const clocks = {
  launch: { epoch: 600n, vintage: 86400n },
  demo: { epoch: 60n, vintage: 180n },
};

const wasm = readFileSync(wasmPath);
console.log(`network ${network}, rpc ${rpcUrl}, parent ${parentId}`);
console.log(`wasm ${wasm.length} bytes, sha256 ${createHash('sha256').update(wasm).digest('hex')}`);

const made = {};
for (const [name, near] of [['launch', contractNear], ['demo', contractNear], ['early', roleNear], ['late', roleNear], ['insider', roleNear], ['panic', roleNear], ['crank', crankNear]]) {
  const r = await createSubAccount(parent, name, near);
  made[name] = r;
  console.log(`created ${r.id} with ${near} NEAR`);
}

const block = await rpc.viewBlock({ finality: 'final' });
const launchTime = BigInt(block.header.timestamp_nanosec);
const report = { network, rpc: rpcUrl, parent: parentId, wasm_sha256: createHash('sha256').update(wasm).digest('hex'), launch_time: String(launchTime), contracts: {} };
for (const d of ['launch', 'demo']) {
  const acct = account(made[d].id, rpc, made[d].secretKey);
  const params = { ...curve, epoch_length: String(clocks[d].epoch * SECOND), vintage_length: String(clocks[d].vintage * SECOND), launch_time: String(launchTime) };
  await deploy(acct, wasm, { method: 'new', args: { params } });
  const got = await view(rpc, made[d].id, 'get_params');
  if (got.epoch_length !== params.epoch_length || got.vintage_length !== params.vintage_length) throw new Error(`${d}: parameters read back differ`);
  const kp = KeyPair.fromString(made[d].secretKey);
  await acct.signAndSendTransaction({ receiverId: made[d].id, actions: [actions.deleteKey(kp.getPublicKey())], waitUntil: 'FINAL', throwOnFailure: true });
  const keys = await rpc.viewAccessKeyList({ accountId: made[d].id, finality: 'final' });
  const list = keys.keys ?? keys;
  if (list.length !== 0) throw new Error(`${d}: ${list.length} access keys remain`);
  report.contracts[d] = { id: made[d].id, epoch_s: String(clocks[d].epoch), vintage_s: String(clocks[d].vintage), access_keys: 0 };
  console.log(`${d}: ${made[d].id} initialised (rounds ${clocks[d].epoch} s, plates ${clocks[d].vintage} s), full-access key deleted, 0 keys left`);
}

const roles = ['early', 'late', 'insider', 'panic'];
const vars = {
  NETWORK: network,
  RPC_URL: rpcUrl,
  EXPLORER_TX: network === 'testnet' ? 'https://testnet.nearblocks.io/txns/' : '',
  EXPLORER_ACCOUNT: network === 'testnet' ? 'https://testnet.nearblocks.io/address/' : '',
  CONTRACT_LAUNCH: made.launch.id,
  CONTRACT_DEMO: made.demo.id,
  ...Object.fromEntries(roles.map((r) => [`ROLE_${r.toUpperCase()}`, made[r].id])),
  CRANK_ACCOUNT: made.crank.id,
};
const secrets = Object.fromEntries([...roles, 'crank'].map((r) => [`KEY_${r.toUpperCase()}`, made[r].secretKey]));
mkdirSync(outDir, { recursive: true });
writeFileSync(new URL(`keys-${network}.json`, outDir), JSON.stringify({ ...report, vars, keys: secrets }, null, 2));
writeFileSync(new URL(`secrets-${network}.json`, outDir), JSON.stringify(secrets, null, 2));
if (network === 'sandbox') {
  const lines = [...Object.entries(vars), ...Object.entries(secrets)].map(([k, v]) => `${k}=${v}`);
  writeFileSync(new URL('../worker/.dev.vars', import.meta.url), `${lines.join('\n')}\n`);
  console.log('wrote worker/.dev.vars');
}
console.log(`wrote .guest/keys-${network}.json and .guest/secrets-${network}.json`);
console.log('copy these into worker/wrangler.toml [vars] if they differ:');
for (const [k, v] of Object.entries(vars)) console.log(`${k} = "${v}"`);
