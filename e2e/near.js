// Thin helpers over near-api-js 7 for the end-to-end scripts.
import { Account, JsonRpcProvider, FailoverRpcProvider, KeyPair, actions, nearToYocto, teraToGas } from 'near-api-js';

export { nearToYocto, teraToGas };

/** One RPC URL, or several separated by commas (fails over between them). */
export function provider(url) {
  const urls = String(url).split(',').map((u) => u.trim()).filter(Boolean);
  if (urls.length === 1) return new JsonRpcProvider({ url: urls[0] });
  return new FailoverRpcProvider(urls.map((u) => new JsonRpcProvider({ url: u })));
}

const RATE_LIMITED = /429|rate limit|Too Many Requests/i;

/** Retry a request that a public RPC rejected for rate limiting (the request did not execute). */
export async function retry(fn, tries = 6, retryable = RATE_LIMITED) {
  for (let i = 0; ; i++) {
    try {
      return await fn();
    } catch (e) {
      if (i >= tries - 1 || !retryable.test(String(e?.message || e))) throw e;
      await sleep(800 * 2 ** i);
    }
  }
}

// A read may also be retried when every endpoint failed or the network dropped: it has no effect.
// Transactions are not retried on these, since a re-signed transaction could execute twice.
const READ_RETRYABLE = /429|rate limit|Too Many Requests|Exceeded \d+ providers|RetriesExceeded|fetch failed|ECONNRESET|ETIMEDOUT|socket hang up|\b50[234]\b/i;

/** Retry a read-only request on rate limits and transient network failures. */
export const retryRead = (fn) => retry(fn, 6, READ_RETRYABLE);

export function account(accountId, rpc, secretKey) {
  return new Account(accountId, rpc, secretKey);
}

export async function view(rpc, contractId, method, args = {}) {
  return retryRead(() => rpc.callFunction({ contractId, method, args }));
}

/** Every failure in the transaction or any of its receipts, with the receipt id. */
export function failures(out) {
  const fails = [];
  const st = out.status ?? out.final_execution_outcome?.status;
  if (st && typeof st === 'object' && 'Failure' in st) fails.push({ where: 'transaction', failure: st.Failure });
  for (const r of out.receipts_outcome || []) {
    const s = r.outcome.status;
    if (s && typeof s === 'object' && 'Failure' in s) fails.push({ where: r.id, executor: r.outcome.executor_id, failure: s.Failure });
  }
  return fails;
}

/** EVENT_JSON logs of one standard in this transaction, in receipt order. */
export function events(out, standard = 'lifo') {
  const evs = [];
  for (const r of out.receipts_outcome || []) {
    for (const log of r.outcome.logs) {
      if (!log.startsWith('EVENT_JSON:')) continue;
      try {
        const e = JSON.parse(log.slice('EVENT_JSON:'.length));
        if (e.standard === standard) evs.push({ ...e, receipt: r.id });
      } catch { /* ignore */ }
    }
  }
  return evs;
}

export function gasBurnt(out) {
  let g = BigInt(out.transaction_outcome?.outcome.gas_burnt || 0);
  for (const r of out.receipts_outcome || []) g += BigInt(r.outcome.gas_burnt);
  return g;
}

export function returnValue(out) {
  const st = out.status;
  if (st && typeof st === 'object' && 'SuccessValue' in st && st.SuccessValue) {
    try { return JSON.parse(Buffer.from(st.SuccessValue, 'base64').toString()); } catch { return null; }
  }
  return null;
}

/**
 * Call a method and return the full outcome. Never throws on a failed receipt:
 * callers decide with failures(out).
 */
export async function call(acct, contractId, methodName, args = {}, { deposit = 0n, gas = teraToGas('300') } = {}) {
  const out = await retry(() => acct.signAndSendTransaction({
    receiverId: contractId,
    actions: [actions.functionCall(methodName, args, BigInt(gas), BigInt(deposit))],
    waitUntil: 'FINAL',
    throwOnFailure: false,
  }));
  return { out, hash: out.transaction?.hash || out.transaction_outcome?.id, fails: failures(out), events: events(out), value: returnValue(out), gas: gasBurnt(out) };
}

/** Create `${prefix}.${parent}` with a fresh full-access key. Returns {id, secretKey}. */
export async function createSubAccount(parent, prefix, near = '5') {
  const kp = KeyPair.fromRandom('ed25519');
  const id = `${prefix}.${parent.accountId}`;
  const out = await parent.signAndSendTransaction({
    receiverId: id,
    actions: [actions.createAccount(), actions.transfer(nearToYocto(near)), actions.addFullAccessKey(kp.getPublicKey())],
    waitUntil: 'FINAL',
    throwOnFailure: true,
  });
  return { id, secretKey: kp.toString(), out };
}

export async function deploy(acct, wasm, init) {
  const acts = [actions.deployContract(wasm)];
  if (init) acts.push(actions.functionCall(init.method, init.args, teraToGas('100'), 0n));
  return acct.signAndSendTransaction({ receiverId: acct.accountId, actions: acts, waitUntil: 'FINAL', throwOnFailure: true });
}

export function b64json(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64');
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
