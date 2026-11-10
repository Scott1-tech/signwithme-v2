// Cloudflare Pages Function: forwards every /api/* request to the backend.
//
// Pages `_redirects` cannot proxy to another host, so this function does it.
// The browser only ever talks to the Pages address, which keeps the app
// same-origin (no CORS, no API host baked into the bundle).
//
// Set BACKEND_URL in the Pages project settings to override the default.
const DEFAULT_BACKEND = 'https://signwithme-v2-api.onrender.com';

export async function onRequest({ request, env }) {
  const backend = (env.BACKEND_URL || DEFAULT_BACKEND).replace(/\/+$/, '');
  const url = new URL(request.url);
  const upstream = new Request(backend + url.pathname + url.search, request);
  // Redirects from the API are passed to the browser as they are.
  return fetch(upstream, { redirect: 'manual' });
}
