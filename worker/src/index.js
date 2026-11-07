// Cloudflare Worker for the LIFO demo. Free-plan features only: static assets, one SQLite-backed Durable Object,
// one cron trigger. The Durable Object does every chain write, one at a time, so that two requests never sign
// with the same nonce. Keys are read from secrets named KEY_EARLY, KEY_LATE, KEY_INSIDER, KEY_PANIC and KEY_CRANK.

import { DurableObject } from 'cloudflare:workers';
import { createRpc, signerFromSecret } from './chain.js';
import { act, config, readState, tick } from './ops.js';
import { route } from './routes.js';

const signers = new Map();

async function signerFor(env, name) {
  const secret = env[`KEY_${name.toUpperCase()}`];
  if (!secret) return null;
  if (!signers.has(name)) signers.set(name, await signerFromSecret(secret));
  return signers.get(name);
}

const memo = new Map();
/** A few seconds of cache inside one isolate; concurrent callers share the same request. */
function cached(key, ttlMs, fn) {
  const hit = memo.get(key);
  if (hit && hit.until > Date.now()) return hit.value;
  const value = fn().catch((e) => {
    memo.delete(key);
    throw e;
  });
  memo.set(key, { until: Date.now() + ttlMs, value });
  return value;
}

export class Gate extends DurableObject {
  chain = Promise.resolve();

  /** Runs jobs one after the other. */
  serial(job) {
    const run = this.chain.then(job, job);
    this.chain = run.catch(() => {});
    return run;
  }

  context() {
    const storage = this.ctx.storage;
    return {
      env: this.env,
      rpc: createRpc(this.env.RPC_URL),
      store: { get: (k) => storage.get(k), put: (k, v) => storage.put(k, v) },
      now: () => Date.now(),
      signer: (name) => signerFor(this.env, name),
    };
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === '/log') {
      const d = new URL(request.url).searchParams.get('d');
      const store = this.ctx.storage;
      return Response.json({ rounds: (await store.get(`rounds:${d}`)) || [], feed: (await store.get(`feed:${d}`)) || [] });
    }
    if (path === '/act') {
      const input = await request.json();
      return Response.json(await this.serial(() => act(this.context(), input)));
    }
    if (path === '/tick') {
      return Response.json(await this.serial(() => tick(this.context())));
    }
    return new Response('not found', { status: 404 });
  }
}

const gate = (env) => env.GATE.get(env.GATE.idFromName('gate'));

export default {
  async fetch(request, env) {
    const stub = gate(env);
    const rpc = createRpc(env.RPC_URL);
    const cfg = config(env);
    const ctx = {
      env,
      rpc,
      now: () => Date.now(),
      params: (d) => cached(`params:${d}`, 10 * 60 * 1000, () => rpc.view(cfg.contracts[d], 'get_params')),
      getLog: async (d) => (await stub.fetch(`https://gate/log?d=${d}`)).json(),
    };
    return route(request, {
      state: (d) => cached(`state:${d}`, 4000, () => readState(ctx, d)),
      act: async (input) => (await stub.fetch('https://gate/act', { method: 'POST', body: JSON.stringify(input) })).json(),
    });
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(gate(env).fetch('https://gate/tick'));
  },
};
