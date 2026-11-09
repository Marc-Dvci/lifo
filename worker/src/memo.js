// Short-lived memory for the state route, inside one isolate. Callers within `ttlMs` share one read of the chain, and
// when every RPC endpoint refuses, the last good answer is served for up to `maxStaleMs`, with the current time and the
// age of the data (`stale_ms`), instead of an error.
export function createMemo(now = () => Date.now()) {
  const fresh = new Map();
  const good = new Map();
  return {
    cached(key, ttlMs, fn) {
      const hit = fresh.get(key);
      if (hit && hit.until > now()) return hit.value;
      const value = fn().catch((e) => {
        fresh.delete(key);
        throw e;
      });
      fresh.set(key, { until: now() + ttlMs, value });
      return value;
    },
    async lastGood(key, maxStaleMs, fn) {
      try {
        const value = await fn();
        good.set(key, { at: now(), value });
        return value;
      } catch (e) {
        const g = good.get(key);
        if (!g || now() - g.at > maxStaleMs) throw e;
        return { ...g.value, now: now(), stale_ms: now() - g.at };
      }
    },
  };
}
