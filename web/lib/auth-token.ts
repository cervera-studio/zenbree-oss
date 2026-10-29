/**
 * Server-side bridge to the Python API: a short-lived JWT (EdDSA, JWKS at
 * /api/auth/jwks) for the current session. Same signature as the earlier implementation's
 * `getToken()` so callers copied from there do not change.
 * Browser side: `authClient.token()`.
 */
import 'server-only';

import { headers } from 'next/headers';
import { cache } from 'react';
import { auth } from './auth';
import { withoutInfraCookie } from './infra-cookie';

// The infra plugin expires `__infra-rid` whenever it finds it in the request headers; nextCookies() turns
// that into a cookie write, and Next renders the page again after any cookie write in a server action (#1058).
async function sessionHeaders(): Promise<Headers> {
  const incoming = await headers();
  const out = new Headers(incoming);
  const cookie = withoutInfraCookie(incoming.get('cookie'));
  if (cookie) out.set('cookie', cookie);
  else out.delete('cookie');
  return out;
}

export async function getToken(): Promise<string | undefined> {
  try {
    const { token } = await auth.api.getToken({ headers: await sessionHeaders() });
    return token;
  } catch {
    return undefined; // no session
  }
}

// Cached for the request: the layout, the page and the i18n config all read it.
export const getSession = cache(async () =>
  auth.api.getSession({ headers: await sessionHeaders() }),
);
