import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, verify, createPublicKey } from 'node:crypto';
import {
  base58Decode, base58Encode, encodeTransaction, failureText, fromBase64, functionCallAction, lifoEvents, signTransaction,
  signerFromSecret, createRpc,
} from '../src/chain.js';

const near = (n) => BigInt(n) * 10n ** 24n;

/** A fresh key in NEAR's text format, and the raw parts to check against. */
function freshKey() {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const seed = privateKey.export({ format: 'der', type: 'pkcs8' }).subarray(-32);
  const pub = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  const secret = `ed25519:${base58Encode(Uint8Array.from([...seed, ...pub]))}`;
  return { secret, pub: Uint8Array.from(pub), publicKey };
}

test('base58 encodes a known text and decodes it back, keeping leading zeros', () => {
  const bytes = new TextEncoder().encode('Hello World!');
  assert.equal(base58Encode(bytes), '2NEpo7TZRRrLZSi2U');
  assert.deepEqual(base58Decode('2NEpo7TZRRrLZSi2U'), bytes);
  assert.deepEqual(base58Decode(base58Encode(Uint8Array.of(0, 0, 7, 255))), Uint8Array.of(0, 0, 7, 255));
  assert.throws(() => base58Decode('0OIl'), /bad base58/);
});

test('a signed transaction carries a signature that verifies against the key and the transaction hash', async () => {
  const k = freshKey();
  const signer = await signerFromSecret(k.secret);
  assert.deepEqual(signer.publicKey, k.pub);
  const tx = {
    signerId: 'early.test.near', nonce: 7n, receiverId: 'demo.test.near', blockHash: new Uint8Array(32).fill(9),
    actions: [functionCallAction('buy', {}, 50n * 10n ** 12n, near(2))],
  };
  const signed = await signTransaction(signer, tx);
  const bytes = fromBase64(signed.base64);
  const body = encodeTransaction({ ...tx, publicKey: k.pub });
  const digest = createHash('sha256').update(body).digest();
  assert.equal(signed.hash, base58Encode(digest));
  assert.equal(bytes.length, body.length + 1 + 64);
  assert.equal(bytes[body.length], 0);
  assert.ok(verify(null, digest, k.publicKey, bytes.subarray(body.length + 1)));
});

test('the borsh bytes equal those of the near-api-js library', async (t) => {
  let lib;
  try {
    lib = await import('../../e2e/node_modules/near-api-js/lib/index.js');
  } catch {
    try { lib = await import('near-api-js'); } catch { return t.skip('near-api-js is not installed'); }
  }
  const k = freshKey();
  const kp = lib.KeyPair.fromString(k.secret);
  const blockHash = new Uint8Array(32).map((_, i) => i);
  const ours = encodeTransaction({
    signerId: 'a.test.near', publicKey: k.pub, nonce: 123456789012n, receiverId: 'b.test.near', blockHash,
    actions: [functionCallAction('request_sell', { amount: '12345' }, 50n * 10n ** 12n, 25n * 10n ** 21n)],
  });
  const theirs = lib.encodeTransaction(lib.createTransaction(
    'a.test.near', kp.getPublicKey(), 'b.test.near', 123456789012n, [lib.actions.functionCall('request_sell', { amount: '12345' }, 50n * 10n ** 12n, 25n * 10n ** 21n)], blockHash,
  ));
  assert.deepEqual(Buffer.from(ours), Buffer.from(theirs));
});

test('a malformed key is refused without echoing it', async () => {
  await assert.rejects(signerFromSecret('ed25519:abc'), (e) => !/abc/.test(e.message) && /bad key/.test(e.message));
  await assert.rejects(signerFromSecret('secp256k1:xyz'), /bad key format/);
  await assert.rejects(signerFromSecret(undefined), /bad key format/);
});

test('the rpc client stops at its request budget and reports node errors', async () => {
  let calls = 0;
  const rpc = createRpc('http://x', async () => {
    calls++;
    return { ok: true, json: async () => (calls === 1 ? { error: { message: 'boom', data: 'details' } } : { result: {} }) };
  }, 2);
  await assert.rejects(rpc.call('status', {}), /details/);
  await rpc.call('status', {});
  await assert.rejects(rpc.call('status', {}), /budget/);
  assert.equal(calls, 2);
});

test('failures and lifo events are read from the receipts of an outcome', () => {
  const ev = { standard: 'lifo', version: '1.0.0', event: 'buy', data: [{ tokens: '5' }] };
  const out = {
    status: { SuccessValue: '' },
    receipts_outcome: [
      { outcome: { logs: ['plain', `EVENT_JSON:${JSON.stringify(ev)}`, 'EVENT_JSON:{"standard":"nep141"}', 'EVENT_JSON:{not json'], status: { SuccessValue: '' } } },
      { outcome: { logs: [], status: { Failure: { ActionError: { kind: { FunctionCallError: { ExecutionError: 'Smart contract panicked: buy too small' } } } } } } },
    ],
  };
  assert.deepEqual(lifoEvents(out), [ev]);
  assert.equal(failureText(out), 'Smart contract panicked: buy too small');
  assert.equal(failureText({ status: { SuccessValue: '' }, receipts_outcome: [] }), null);
});
