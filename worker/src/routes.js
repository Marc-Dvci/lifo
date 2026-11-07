// Routing and validation. Pure: the chain and the Durable Object are reached through `deps`, so that the tests
// run without a network.

export const DEPLOYMENTS = ['launch', 'demo'];
export const ROLE_NAMES = ['early', 'late', 'insider', 'panic'];
export const ACTIONS = ['buy', 'sell'];

const MAX_BODY = 256;

const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', ...headers } });

/** Exactly {d, role, action}, each one of the allowed words. Returns {value} or {error}. */
export function parseAct(raw) {
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return { error: 'body is not JSON' };
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) return { error: 'body must be an object' };
  const keys = Object.keys(body).sort().join(',');
  if (keys !== 'action,d,role') return { error: 'body must be exactly d, role and action' };
  if (!DEPLOYMENTS.includes(body.d)) return { error: 'd must be launch or demo' };
  if (!ROLE_NAMES.includes(body.role)) return { error: 'role must be early, late, insider or panic' };
  if (!ACTIONS.includes(body.action)) return { error: 'action must be buy or sell' };
  return { value: { d: body.d, role: body.role, action: body.action } };
}

export async function route(request, deps) {
  const url = new URL(request.url);
  if (url.pathname === '/api/state') {
    if (request.method !== 'GET') return json(405, { error: 'use GET' }, { allow: 'GET' });
    const d = url.searchParams.get('d') || 'demo';
    if (!DEPLOYMENTS.includes(d)) return json(400, { error: 'd must be launch or demo' });
    try {
      return json(200, await deps.state(d), { 'cache-control': 'public, max-age=3' });
    } catch {
      return json(503, { error: 'the chain could not be read, try again in a moment' });
    }
  }
  if (url.pathname === '/api/act') {
    if (request.method !== 'POST') return json(405, { error: 'use POST' }, { allow: 'POST' });
    const raw = await request.text();
    if (raw.length > MAX_BODY) return json(413, { error: 'body too large' });
    const parsed = parseAct(raw);
    if (parsed.error) return json(400, { error: parsed.error });
    const ip = request.headers.get('cf-connecting-ip') || 'local';
    const r = await deps.act({ ...parsed.value, ip });
    return json(r.status, r.body, { 'cache-control': 'no-store', ...(r.body.retry_after ? { 'retry-after': String(r.body.retry_after) } : {}) });
  }
  return json(404, { error: 'not found' });
}
