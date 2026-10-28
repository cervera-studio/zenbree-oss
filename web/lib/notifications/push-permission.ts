import { useSyncExternalStore } from 'react';
import { pushSupported } from '@/lib/notifications/push';

export type PushPermission = NotificationPermission | 'unsupported';

const listeners = new Set<() => void>();

function readPermission(): PushPermission {
  return pushSupported() ? Notification.permission : 'unsupported';
}

function subscribePushPermission(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener('focus', onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener('focus', onChange);
  };
}

/** Tells every mounted subscriber to read the permission again, after an action that may change it. */
function rereadPushPermission() {
  for (const notify of listeners) notify();
}

/** The browser's notification permission, re-read whenever the tab regains focus. */
export function usePushPermission() {
  const permission = useSyncExternalStore(
    subscribePushPermission,
    readPermission,
    (): PushPermission => 'default',
  );
  return { permission, reread: rereadPushPermission };
}
