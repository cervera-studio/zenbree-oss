/**
 * The service worker that shows a push with no Zenbree tab open. It is plain JavaScript in a string because
 * the web image has no `public/` directory: `app/sw.js/route.ts` serves it. The payload is the API's
 * (`app/notifications/push.py`): `{id, title, body, href}`, never a reply's text.
 */
export const SERVICE_WORKER_SOURCE = `
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let m = {};
  try {
    m = event.data ? event.data.json() : {};
  } catch {}
  // A push that shows nothing makes the browser show its own "updated in the background" notice.
  event.waitUntil(
    self.registration.showNotification(m.title || 'Zenbree', {
      body: m.body || '',
      tag: m.id,
      icon: '/apple-icon.png',
      data: { href: m.href },
    }),
  );
});

// Resolved the way the browser will (tabs and newlines dropped, \\ read as /), so a raw pattern cannot
// be slipped past: only a path on this origin is followed.
function inApp(href) {
  if (typeof href !== 'string') return '/';
  try {
    const url = new URL(href, self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search + url.hash : '/';
  } catch {
    return '/';
  }
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const path = inApp(event.notification.data && event.notification.data.href);
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => new URL(w.url).origin === self.location.origin);
      if (!open) return self.clients.openWindow(path);
      return open
        .focus()
        .then((w) => (w || open).navigate(path))
        .catch(() => self.clients.openWindow(path));
    }),
  );
});
`;
