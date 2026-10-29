import type { CaptureResult } from 'posthog-js';

// The prospect unsubscribe page (#220): its token is a credential that never expires, and the
// prospect never agreed to tracking. Its host serves only these paths (`proxy.ts`).
const UNSUBSCRIBE_PAGE = /^\/(u|x)\/[^/]+/;
// Absolute (`https://…`) or relative (`/…`): anything else is plain text and stays as it is.
const URL_LIKE = /^([a-z][a-z\d+.-]*:\/\/|\/)/i;
// Autocapture's `$elements_chain` is a selector string with hrefs inside, not a URL.
const CHAIN_TOKEN = /((?:t|%74)oken=)[^&#\s"';]*/gi;
const CHAIN_UNSUBSCRIBE = /\/(u|x)\/[^/?#\s"';]+/g;

export function isUnsubscribePage(pathname: string): boolean {
  return UNSUBSCRIBE_PAGE.test(pathname);
}

const BASE = 'https://x';

function pathnameOf(value: unknown): string | null {
  return typeof value === 'string' && URL_LIKE.test(value) && URL.canParse(value, BASE)
    ? new URL(value, BASE).pathname
    : null;
}

// `?token=` carries the Better Auth password-reset token (#430); `/u/<token>` the unsubscribe one.
function scrubUrl(value: string): string {
  if (!URL_LIKE.test(value) || !URL.canParse(value, BASE)) return value;
  const url = new URL(value, BASE);
  // URLSearchParams decodes keys, so this also catches `%74oken`.
  const hasToken = url.searchParams.has('token');
  const unsubscribe = isUnsubscribePage(url.pathname);
  if (!hasToken && !unsubscribe) return value;
  url.searchParams.delete('token');
  if (unsubscribe) url.pathname = url.pathname.replace(UNSUBSCRIBE_PAGE, '/$1/redacted');
  return value.startsWith('/') && !value.startsWith('//')
    ? url.pathname + url.search + url.hash
    : url.href;
}

function scrub(value: unknown, key = ''): unknown {
  if (typeof value === 'string') {
    return key === '$elements_chain'
      ? value.replace(CHAIN_TOKEN, '$1redacted').replace(CHAIN_UNSUBSCRIBE, '/$1/redacted')
      : scrubUrl(value);
  }
  if (Array.isArray(value)) return value.map((item) => scrub(item));
  if (value && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([name, v]) => [name, scrub(v, name)]));
  }
  return value;
}

// `before_send` for every event: drops it on the unsubscribe page, and walks properties, `$set`
// and `$set_once` (objects and arrays too, so replay frames and `$elements`) to strip the tokens
// from every URL-like string and from `$elements_chain`.
export function scrubEvent(event: CaptureResult | null): CaptureResult | null {
  if (!event) return null;
  const { $current_url: current, $pathname: pathname } = event.properties;
  if ([current, pathname].some((v) => isUnsubscribePage(pathnameOf(v) ?? ''))) return null;
  return {
    ...event,
    properties: scrub(event.properties) as CaptureResult['properties'],
    ...(event.$set && { $set: scrub(event.$set) as CaptureResult['$set'] }),
    ...(event.$set_once && { $set_once: scrub(event.$set_once) as CaptureResult['$set_once'] }),
  };
}
