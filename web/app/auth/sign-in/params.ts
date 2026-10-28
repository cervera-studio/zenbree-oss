// Shared by the proxy, the app layout and the sign-in form, so the URL shape lives in one place.

// Mirrors Better Auth's isSafeRelativeURL (auth/trusted-origins).
function isSafeRelative(value: string): boolean {
  const path = value.split(/[?#]/, 1)[0] ?? '';
  return (
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.includes('\\') &&
    // biome-ignore lint/suspicious/noControlCharactersInRegex: rejecting control characters is the point
    !/[\u0000-\u001f\u007f-\u009f]/.test(value) &&
    !/%2f|%5c/i.test(path)
  );
}

/** A same-origin relative path to return to after sign-in, or '/' (no open redirect). */
// A repeated `?callbackUrl=` reaches the page as an array.
export function safeCallback(raw: string | string[] | null | undefined): string {
  if (typeof raw !== 'string' || !isSafeRelative(raw)) return '/';
  const base = 'http://zenbree.invalid';
  try {
    const url = new URL(raw, base);
    const out = url.pathname + url.search + url.hash;
    // Check the normalised result too: dot segments can turn `/.//evil.com` into `//evil.com`.
    return url.origin === base && isSafeRelative(out) ? out : '/';
  } catch {
    return '/';
  }
}

export const LAST_EMAIL_KEY = 'zenbree.lastEmail';

function withCallback(path: string, callbackUrl: string, expired = false): string {
  const params = new URLSearchParams();
  if (expired) params.set('notice', 'expired');
  const target = safeCallback(callbackUrl);
  if (target !== '/') params.set('callbackUrl', target);
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export function signInPath(callbackUrl: string, expired = false): string {
  return withCallback('/auth/sign-in', callbackUrl, expired);
}

export function expiredPath(callbackUrl: string): string {
  return withCallback('/auth/expired', callbackUrl);
}

/** Sign-up, and the verify page after it, carry the page to return to (#323: an invitation link) the same way. */
export function signUpPath(callbackUrl: string): string {
  return withCallback('/auth/sign-up', callbackUrl);
}

export function verifyPath(callbackUrl: string): string {
  return withCallback('/auth/verify', callbackUrl);
}

export interface BanInfo {
  reason: string | null;
  expires: string | null;
}

// ponytail: Better Auth's BANNED_USER error carries only a message string, so
// lib/auth.ts packs the ban details into it as JSON. It is only built after the
// password (or OAuth) check passes, so it never tells a stranger about an account.
export function encodeBan(user: BannedUser): string {
  return JSON.stringify(banInfo(user));
}

interface BannedUser {
  banReason?: string | null;
  banExpires?: Date | string | null;
}

function banInfo(user: BannedUser): BanInfo {
  return {
    reason: user.banReason ?? null,
    expires: user.banExpires ? new Date(user.banExpires).toISOString() : null,
  };
}

/** An expired ban is no ban, as `_NOT_BANNED` in api/app/auth/middleware.py. */
export function activeBan(
  user: BannedUser & { banned?: boolean | null },
  now: Date,
): BanInfo | null {
  if (user.banned !== true) return null;
  if (user.banExpires && new Date(user.banExpires) < now) return null;
  return banInfo(user);
}

export function decodeBan(message: string | undefined): BanInfo {
  try {
    const v = JSON.parse(message ?? '');
    return {
      reason: typeof v?.reason === 'string' && v.reason ? v.reason : null,
      expires:
        typeof v?.expires === 'string' && !Number.isNaN(Date.parse(v.expires)) ? v.expires : null,
    };
  } catch {
    return { reason: null, expires: null };
  }
}
