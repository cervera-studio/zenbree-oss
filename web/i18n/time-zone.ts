// HTTP has no request header for the user's time zone, unlike Accept-Language for the locale.
export const TIME_ZONE_COOKIE = 'NEXT_TIME_ZONE';

const KNOWN = new Set([...Intl.supportedValuesOf('timeZone'), 'UTC']);
const SHAPE = /^(UTC|[A-Z][A-Za-z_]+(\/[A-Za-z0-9_+-]+)+)$/;

/**
 * The runtime's canonical IANA name for a saved zone, or null. The shape check keeps out what Intl would also take
 * ("+05:30", "EST"); canonicalizing means Asia/Kolkata and a later ICU rename of it are the same zone.
 */
export function validTimeZone(value: string | null | undefined): string | null {
  if (!value || !SHAPE.test(value)) return null;
  try {
    const canonical = new Intl.DateTimeFormat('en', { timeZone: value }).resolvedOptions().timeZone;
    return KNOWN.has(canonical) ? canonical : null;
  } catch {
    return null;
  }
}

// The cookie holds the browser's own name, which may be a legacy alias (Asia/Calcutta): Intl is the judge here.
export function resolveTimeZone(value: string | undefined): string {
  if (!value) return 'UTC';
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value });
    return value;
  } catch {
    return 'UTC';
  }
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

// Browsers without the Cookie Store API (e.g. Safari < 18.4) write no cookie,
// so their server-rendered times stay UTC.
export function rememberBrowserTimeZone(): void {
  void globalThis.cookieStore
    ?.set({
      name: TIME_ZONE_COOKIE,
      value: browserTimeZone(),
      path: '/',
      expires: Date.now() + 365 * 24 * 60 * 60 * 1000,
      sameSite: 'lax',
    })
    // Chromium rejects the write when the user blocks cookies; error reporting would log it on every load.
    .catch(() => {});
}
