// NEAR transactions without a library: base58, the borsh encoding of a function-call transaction, ed25519 signing
// with WebCrypto, and the JSON-RPC calls the Worker needs. Pure JavaScript, no dependency, nothing to bundle.

const ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const enc = new TextEncoder();

export function base58Encode(bytes) {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
  const digits = [];
  for (let i = zeros; i < bytes.length; i++) {
    let carry = bytes[i];
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8;
      digits[j] = carry % 58;
      carry = (carry / 58) | 0;
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = (carry / 58) | 0;
    }
  }
  return '1'.repeat(zeros) + digits.reverse().map((d) => ALPHABET[d]).join('');
}

export function base58Decode(text) {
  let zeros = 0;
  while (zeros < text.length && text[zeros] === '1') zeros++;
  const bytes = [];
  for (let i = zeros; i < text.length; i++) {
    const v = ALPHABET.indexOf(text[i]);
    if (v < 0) throw new Error('bad base58');
    let carry = v;
    for (let j = 0; j < bytes.length; j++) {
      carry += bytes[j] * 58;
      bytes[j] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  return new Uint8Array([...new Array(zeros).fill(0), ...bytes.reverse()]);
}

export function toBase64(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function fromBase64(text) {
  const s = atob(text);
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

// --- borsh ----------------------------------------------------------------------------------------------------------

function le(value, size) {
  let v = BigInt(value);
  const out = new Uint8Array(size);
  for (let i = 0; i < size; i++) {
    out[i] = Number(v & 0xffn);
    v >>= 8n;
  }
  if (v !== 0n) throw new Error('integer too large');
  return out;
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

const str = (s) => {
  const b = enc.encode(s);
  return concat(le(b.length, 4), b);
};

/** A function-call action: enum index 2, method, args (bytes), gas (u64), deposit (u128). */
export function functionCallAction(method, args, gas, deposit) {
  const argBytes = typeof args === 'string' ? enc.encode(args) : enc.encode(JSON.stringify(args ?? {}));
  return concat(Uint8Array.of(2), str(method), concat(le(argBytes.length, 4), argBytes), le(gas, 8), le(deposit, 16));
}

/** The borsh bytes of a transaction with an ed25519 key. `actions` are already encoded. */
export function encodeTransaction({ signerId, publicKey, nonce, receiverId, blockHash, actions }) {
  if (publicKey.length !== 32 || blockHash.length !== 32) throw new Error('bad key or block hash length');
  return concat(
    str(signerId),
    Uint8Array.of(0),
    publicKey,
    le(nonce, 8),
    str(receiverId),
    blockHash,
    le(actions.length, 4),
    ...actions,
  );
}

// --- keys and signing -----------------------------------------------------------------------------------------------

const PKCS8_ED25519 = Uint8Array.of(0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20);

/** A NEAR secret key `ed25519:<base58 of seed and public key>` becomes a signer. The text is never kept or returned. */
export async function signerFromSecret(secret) {
  const m = /^ed25519:([1-9A-HJ-NP-Za-km-z]+)$/.exec(String(secret || '').trim());
  if (!m) throw new Error('bad key format');
  const raw = base58Decode(m[1]);
  if (raw.length !== 64) throw new Error('bad key length');
  const key = await crypto.subtle.importKey('pkcs8', concat(PKCS8_ED25519, raw.slice(0, 32)), { name: 'Ed25519' }, false, ['sign']);
  const publicKey = raw.slice(32);
  return {
    publicKey,
    publicKeyText: `ed25519:${base58Encode(publicKey)}`,
    sign: async (bytes) => new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, key, bytes)),
  };
}

async function sha256(bytes) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

/** Signs a transaction. Returns the base64 signed transaction and its hash (base58). */
export async function signTransaction(signer, tx) {
  const body = encodeTransaction({ ...tx, publicKey: signer.publicKey });
  const hash = await sha256(body);
  const signature = await signer.sign(hash);
  return { base64: toBase64(concat(body, Uint8Array.of(0), signature)), hash: base58Encode(hash) };
}

// --- JSON-RPC -------------------------------------------------------------------------------------------------------

/**
 * A small RPC client. `fetchFn` is injectable for tests. `budget` caps the number of requests one invocation makes
 * (the free plan allows 50 external subrequests per invocation).
 */
export function createRpc(url, fetchFn = fetch, budget = 45) {
  let used = 0;
  let id = 0;
  async function call(method, params) {
    if (++used > budget) throw new Error('subrequest budget used');
    const res = await fetchFn(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: String(++id), method, params }),
    });
    if (!res.ok) throw new Error(`rpc http ${res.status}`);
    const body = await res.json();
    if (body.error) {
      const e = new Error(body.error.data && typeof body.error.data === 'string' ? body.error.data : body.error.message || 'rpc error');
      e.rpc = body.error;
      throw e;
    }
    return body.result;
  }
  return {
    call,
    get used() { return used; },
    async view(contractId, method, args = {}) {
      const r = await call('query', {
        request_type: 'call_function',
        finality: 'optimistic',
        account_id: contractId,
        method_name: method,
        args_base64: toBase64(enc.encode(JSON.stringify(args))),
      });
      return JSON.parse(new TextDecoder().decode(Uint8Array.from(r.result)));
    },
    async blockHash() {
      const b = await call('block', { finality: 'final' });
      return b.header.hash;
    },
    async nonce(accountId, publicKeyText) {
      const r = await call('query', { request_type: 'view_access_key', finality: 'optimistic', account_id: accountId, public_key: publicKeyText });
      return BigInt(r.nonce);
    },
    async balance(accountId) {
      const r = await call('query', { request_type: 'view_account', finality: 'optimistic', account_id: accountId });
      return BigInt(r.amount);
    },
  };
}

/** Text of the first failure in an outcome, or null. */
export function failureText(out) {
  const find = (status) => {
    if (!status || typeof status !== 'object' || !('Failure' in status)) return null;
    const f = status.Failure;
    const msg = f?.ActionError?.kind?.FunctionCallError?.ExecutionError ?? f?.ActionError?.kind ?? f;
    return typeof msg === 'string' ? msg : JSON.stringify(msg);
  };
  // A receipt names the contract's message; the transaction's own status only repeats it in a coarser form.
  for (const r of out.receipts_outcome || []) {
    const t = find(r.outcome.status);
    if (t) return t;
  }
  return find(out.status);
}

/** EVENT_JSON logs of the `lifo` standard, in receipt order. */
export function lifoEvents(out) {
  const events = [];
  for (const r of out.receipts_outcome || []) {
    for (const log of r.outcome.logs || []) {
      if (!log.startsWith('EVENT_JSON:')) continue;
      try {
        const e = JSON.parse(log.slice('EVENT_JSON:'.length));
        if (e.standard === 'lifo') events.push(e);
      } catch { /* a log that is not JSON is ignored */ }
    }
  }
  return events;
}

/**
 * Signs and sends one function call and waits for it to run. Returns {hash, error, events}.
 * A transaction that fails on the chain is a result, not an exception; a request that could not be sent throws.
 */
export async function sendCall(rpc, signer, { signerId, receiverId, method, args, gas, deposit, blockHash }) {
  const [nonce, hash] = await Promise.all([rpc.nonce(signerId, signer.publicKeyText), blockHash ?? rpc.blockHash()]);
  const signed = await signTransaction(signer, {
    signerId,
    nonce: nonce + 1n,
    receiverId,
    blockHash: base58Decode(hash),
    actions: [functionCallAction(method, args, gas, deposit)],
  });
  const out = await rpc.call('send_tx', { signed_tx_base64: signed.base64, wait_until: 'EXECUTED_OPTIMISTIC' });
  return { hash: signed.hash, error: failureText(out), events: lifoEvents(out) };
}
