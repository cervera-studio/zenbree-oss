import { api } from '@/lib/api/browser';
import { getStored, removeStored, setStored } from '@/lib/storage/storage';
import { pushConfigSchema } from './push-schema';

const WORKER = '/sw.js';

export function pushSupported() {
  return (
    typeof Notification !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof PushManager !== 'undefined'
  );
}

/** The Web Push API takes the VAPID key as bytes. */
export function keyBytes(base64Url: string) {
  const padded = base64Url
    .replaceAll('-', '+')
    .replaceAll('_', '/')
    .padEnd(Math.ceil(base64Url.length / 4) * 4, '=');
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function localSubscription() {
  const registration = await navigator.serviceWorker.getRegistration(WORKER);
  return (await registration?.pushManager.getSubscription()) ?? null;
}

/** This browser's push endpoint, or null when it has no subscription (or none it can read). */
export async function localEndpoint() {
  if (!pushSupported()) return null;
  try {
    return (await localSubscription())?.endpoint ?? null;
  } catch {
    return null;
  }
}

/** True when this browser's subscription is the signed-in user's, not one another account left behind. */
export async function pushActive() {
  if (!pushSupported() || Notification.permission !== 'granted') return false;
  const endpoint = await localEndpoint();
  if (!endpoint) return false;
  const res = await api('/v1/notifications/push', { schema: pushConfigSchema });
  return res.success && res.data.subscribed.includes(endpoint);
}

// Per browser, not per user: "turn off on this browser" must outlast the load-time re-sync below.
const TURNED_OFF = 'zb-push-off';

export function pushTurnedOff() {
  return getStored(TURNED_OFF, 'local') === '1';
}

function rememberTurnedOff(off: boolean) {
  if (off) setStored(TURNED_OFF, '1', 'local');
  else removeStored(TURNED_OFF, 'local');
}

function sameKey(subscription: PushSubscription, key: Uint8Array) {
  const current = subscription.options.applicationServerKey;
  if (!current) return false;
  const bytes = new Uint8Array(current);
  return bytes.length === key.length && bytes.every((b, i) => b === key[i]);
}

/** Subscribes this browser with `publicKey` and saves it for the signed-in user. Permission must be
 *  granted. A subscription made with another key is replaced, on the server too: after a VAPID rotation
 *  the push service refuses every send to it. Returns this browser's endpoint, or null. */
async function subscribe(publicKey: string) {
  try {
    const registration = await navigator.serviceWorker.register(WORKER, { scope: '/' });
    await navigator.serviceWorker.ready;
    const key = keyBytes(publicKey);
    let subscription = await registration.pushManager.getSubscription();
    if (subscription && !sameKey(subscription, key)) {
      await forget(subscription);
      subscription = null;
    }
    subscription ??= await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: key,
    });
    const json = subscription.toJSON();
    const saved = await api('/v1/notifications/push', {
      method: 'PUT',
      body: {
        endpoint: subscription.endpoint,
        p256dh: json.keys?.p256dh ?? '',
        auth: json.keys?.auth ?? '',
      },
    });
    return saved.success ? subscription.endpoint : null;
  } catch {
    return null;
  }
}

/** Needs a user gesture: it may show the permission prompt. With no `publicKey` (push is inert) it
 *  only asks, for the in-page alert. Returns this browser's endpoint, or null. */
export async function enablePush(publicKey: string | null) {
  if (!pushSupported()) return null;
  if ((await Notification.requestPermission()) !== 'granted' || !publicKey) return null;
  rememberTurnedOff(false);
  return subscribe(publicKey);
}

/** Runs on every signed-in load, with no prompt: a browser that granted permission (here, or for the
 *  in-page alert before push existed) gets a subscription with the current key, saved for this user. */
export async function syncPush() {
  if (!pushSupported() || Notification.permission !== 'granted' || pushTurnedOff()) return;
  const config = await api('/v1/notifications/push', { schema: pushConfigSchema });
  if (!config.success || !config.data.publicKey) return;
  const local = await localSubscription().catch(() => null);
  const current =
    local &&
    config.data.subscribed.includes(local.endpoint) &&
    sameKey(local, keyBytes(config.data.publicKey));
  if (!current) await subscribe(config.data.publicKey);
}

// A sign-out waits on this, so a slow API gets a bound. The browser's unsubscribe below is what stops
// the alerts: the push service then answers 410 and the server drops the row.
const FORGET_WAIT_MS = 3000;

/** On the server first, while the session can still say whose it is, then in the browser. */
async function forget(subscription: PushSubscription) {
  const deleted = api('/v1/notifications/push', {
    method: 'DELETE',
    body: { endpoint: subscription.endpoint },
  }).catch(() => null);
  await Promise.race([deleted, new Promise((resolve) => setTimeout(resolve, FORGET_WAIT_MS))]);
  await subscription.unsubscribe();
}

/** Forgets this browser's subscription. Never throws: push never stops a sign-out. */
export async function disablePush() {
  if (!pushSupported()) return;
  try {
    const subscription = await localSubscription();
    if (subscription) await forget(subscription);
  } catch {}
}

/** "Turn off on this browser": no push and no in-page alert here until the user turns it back on. */
export async function turnOffPush() {
  rememberTurnedOff(true);
  await disablePush();
}

export async function sendTestPush(endpoint: string) {
  return (await api('/v1/notifications/push/test', { method: 'POST', body: { endpoint } })).success;
}
