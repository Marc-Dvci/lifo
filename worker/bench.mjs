// CPU cost of the Worker's own work for one action: build, sign and base64-encode a transaction, parse an outcome,
// and apply the rate limit. Measured with process.cpuUsage in Node; network waits are not CPU and are not included.
// workerd does not report CPU time for a local run, so this is the same code under Node, not a Cloudflare figure.
//   node worker/bench.mjs
import { generateKeyPairSync } from 'node:crypto';
import { base58Encode, base58Decode, functionCallAction, lifoEvents, signTransaction, signerFromSecret } from './src/chain.js';
import { LIMITS, RateLimiter, ipKey } from './src/limits.js';

const { privateKey, publicKey } = generateKeyPairSync('ed25519');
const seed = privateKey.export({ format: 'der', type: 'pkcs8' }).subarray(-32);
const pub = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
const secret = `ed25519:${base58Encode(Uint8Array.from([...seed, ...pub]))}`;
const blockHash = base58Decode(base58Encode(new Uint8Array(32).fill(7)));
const outcome = { status: { SuccessValue: '' }, receipts_outcome: Array.from({ length: 6 }, () => ({ outcome: { logs: ['EVENT_JSON:{"standard":"nep141","version":"1.0.0","event":"ft_mint","data":[{}]}'], status: { SuccessValue: '' } } })) };

async function one(signer, rl, i) {
  await ipKey(`10.0.0.${i % 250}`);
  rl.take(`k${i % 250}`, i * 1000);
  await signTransaction(signer, {
    signerId: 'early.test.near', nonce: BigInt(i), receiverId: 'demo.test.near', blockHash,
    actions: [functionCallAction('buy', {}, 50n * 10n ** 12n, 2n * 10n ** 24n)],
  });
  lifoEvents(outcome);
}

const signer = await signerFromSecret(secret);
const rl = new RateLimiter(LIMITS);
for (let i = 0; i < 200; i++) await one(signer, rl, i); // warm up
const N = 2000;
const c0 = process.cpuUsage();
const t0 = performance.now();
for (let i = 0; i < N; i++) await one(signer, rl, i);
const c = process.cpuUsage(c0);
const key0 = process.cpuUsage();
await signerFromSecret(secret);
const k = process.cpuUsage(key0);
console.log(JSON.stringify({
  runs: N,
  cpu_ms_per_action: Number(((c.user + c.system) / 1000 / N).toFixed(3)),
  wall_ms_per_action: Number(((performance.now() - t0) / N).toFixed(3)),
  key_import_cpu_ms: Number(((k.user + k.system) / 1000).toFixed(3)),
  node: process.version,
}));
