// Cloudflare Worker in front of the built frontend.
//
// /api/* is forwarded to the backend, so the browser only ever talks to this
// address: the app stays same-origin (no CORS, no API host in the bundle).
// Everything else is served from the static build in ./dist (see wrangler.jsonc).
//
// Set a BACKEND_URL variable on the Worker to override the default.
const DEFAULT_BACKEND = 'https://signwithme-v2-api.onrender.com';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
      const backend = (env.BACKEND_URL || DEFAULT_BACKEND).replace(/\/+$/, '');
      const upstream = new Request(backend + url.pathname + url.search, request);
      // Redirects from the API are passed to the browser as they are.
      return fetch(upstream, { redirect: 'manual' });
    }
    return env.ASSETS.fetch(request);
  },
};
