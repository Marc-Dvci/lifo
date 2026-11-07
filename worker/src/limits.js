// Sliding-window rate limits: one per visitor (a hash of the IP address) and one for the whole demo, so that no
// visitor can drain the demo accounts. The state is a plain object the caller keeps in Durable Object storage.

export const LIMITS = {
  perIp: { max: 8, windowMs: 10 * 60 * 1000 },
  global: { max: 120, windowMs: 60 * 60 * 1000 },
};

const MAX_IPS = 2000;

export class RateLimiter {
  constructor(limits = LIMITS, state = { ips: {}, global: [] }) {
    this.limits = limits;
    this.state = state;
  }

  /** Counts one action if it is allowed. Returns {ok:true} or {ok:false, scope, retryAfter} (seconds). */
  take(ipKey, now) {
    const { perIp, global } = this.limits;
    const ips = this.state.ips;
    const mine = (ips[ipKey] || []).filter((t) => t > now - perIp.windowMs);
    const all = this.state.global.filter((t) => t > now - global.windowMs);
    this.state.global = all;
    if (mine.length) ips[ipKey] = mine; else delete ips[ipKey];
    if (mine.length >= perIp.max) {
      return { ok: false, scope: 'visitor', retryAfter: Math.ceil((mine[0] + perIp.windowMs - now) / 1000) };
    }
    if (all.length >= global.max) {
      return { ok: false, scope: 'demo', retryAfter: Math.ceil((all[0] + global.windowMs - now) / 1000) };
    }
    mine.push(now);
    ips[ipKey] = mine;
    all.push(now);
    const keys = Object.keys(ips);
    if (keys.length > MAX_IPS) {
      // Drop the visitors whose newest action is oldest, so that the state stays small.
      keys.sort((a, b) => ips[a][ips[a].length - 1] - ips[b][ips[b].length - 1]);
      for (const k of keys.slice(0, keys.length - MAX_IPS)) delete ips[k];
    }
    return { ok: true };
  }
}

/** The key stored for an address: a truncated SHA-256, so the address itself is never stored. */
export async function ipKey(ip) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`lifo-demo|${ip}`)));
  return [...d.slice(0, 8)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
