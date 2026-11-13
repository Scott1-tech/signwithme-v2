// Cloudflare Worker in front of the built frontend.
//
// /api/* is forwarded to the backend, so the browser only ever talks to this
// address: the app stays same-origin (no CORS, no API host in the bundle).
// Everything else is served from the static build in ./dist (see wrangler.jsonc).
//
// The backend runs on Render's free plan, which sleeps after ~15 idle minutes
// and drops the first connections while it wakes. Two things cover that:
//   * a request whose connection fails is retried for up to ~70 s, so the
//     browser waits instead of showing "Failed to fetch";
//   * a scheduled ping (wrangler.jsonc `triggers.crons`) keeps it awake.
//
// Set a BACKEND_URL variable on the Worker to override the default.
const DEFAULT_BACKEND = 'https://signwithme-v2-api.onrender.com';
const RETRY_FOR_MS = 70_000;
const RETRY_DELAY_MS = 3_000;

const backendOf = (env) => (env.BACKEND_URL || DEFAULT_BACKEND).replace(/\/+$/, '');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function forward(request, env) {
  const url = new URL(request.url);
  const target = backendOf(env) + url.pathname + url.search;
  // The body is read once so the same request can be sent again on a retry.
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  const body = hasBody ? await request.arrayBuffer() : undefined;
  const started = Date.now();
  for (;;) {
    try {
      // Only a failed CONNECTION is retried: the request never reached the
      // app, so sending it again cannot do anything twice.
      const response = await fetch(target, {
        method: request.method,
        headers: request.headers,
        body,
        redirect: 'manual',
      });
      // Render answers 502/503 while an instance is still starting.
      const waking = response.status === 502 || response.status === 503;
      if (!waking || Date.now() - started > RETRY_FOR_MS) return response;
    } catch (error) {
      if (Date.now() - started > RETRY_FOR_MS) {
        return new Response(
          JSON.stringify({
            error: 'backend_unavailable',
            message: 'The server is starting up. Please try again in a minute.',
          }),
          { status: 503, headers: { 'content-type': 'application/json' } },
        );
      }
    }
    await sleep(RETRY_DELAY_MS);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
      return forward(request, env);
    }
    return env.ASSETS.fetch(request);
  },

  // Keep-awake ping (see the header).
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(fetch(backendOf(env) + '/health').catch(() => undefined));
  },
};
