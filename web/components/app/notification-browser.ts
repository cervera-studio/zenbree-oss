import { type AppNotification, safeHref } from '@/lib/notifications/group';
import { pushActive, pushTurnedOff } from '@/lib/notifications/push';
import type { useText } from './notification-text';

// In-page alert for a browser with no push subscription, unless the user turned this browser off. A
// subscribed one gets the service worker's notification instead; `tag` collapses the same item across tabs.
export async function showBrowser(
  fresh: AppNotification[],
  text: ReturnType<typeof useText>,
  go: (href: string) => void,
) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  if (pushTurnedOff() || (await pushActive())) return;
  for (const n of fresh) {
    if (!n.browser || n.read) continue;
    const { title, body } = text(n);
    const shown = new Notification(title, { body, tag: n.id });
    shown.onclick = () => {
      window.focus();
      go(safeHref(n.href));
    };
  }
}
