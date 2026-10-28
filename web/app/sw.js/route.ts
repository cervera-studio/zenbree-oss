import { SERVICE_WORKER_SOURCE } from '@/lib/notifications/service-worker';

// A route, not `public/sw.js`: the image copies only `.next/standalone` and `.next/static`. The proxy skips
// dotted paths, so this is reachable signed out, which a service worker fetch needs. no-cache: the browser
// must see a new worker on the next visit.
export function GET() {
  return new Response(SERVICE_WORKER_SOURCE, {
    headers: {
      'Content-Type': 'text/javascript; charset=utf-8',
      'Cache-Control': 'no-cache',
      'Service-Worker-Allowed': '/',
    },
  });
}
