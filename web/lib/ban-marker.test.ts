import { type BetterAuthOptions, betterAuth } from 'better-auth';
import { memoryAdapter } from 'better-auth/adapters/memory';
import { APIError } from 'better-auth/api';
import { admin } from 'better-auth/plugins';
import { describe, expect, it } from 'vite-plus/test';
import { activeBan } from '../app/auth/sign-in/params';
import { banMarker } from './ban-marker';

const NOW = new Date('2026-10-14T12:00:00Z');

describe('activeBan', () => {
  it.each([
    [
      'a ban with no end',
      { banned: true, banReason: 'Spam', banExpires: null },
      { reason: 'Spam', expires: null },
    ],
    [
      'a ban that ends later',
      { banned: true, banReason: null, banExpires: new Date('2026-10-21T12:00:00Z') },
      { reason: null, expires: '2026-10-21T12:00:00.000Z' },
    ],
    [
      'a ban that ended',
      { banned: true, banReason: 'Spam', banExpires: new Date('2026-10-14T11:59:59Z') },
      null,
    ],
    ['no ban', { banned: false, banReason: null, banExpires: null }, null],
  ])('%s', (_, user, expected) => {
    expect(activeBan(user, NOW)).toEqual(expected);
  });
});

const MARKER = 'better-auth.signed_in_user';
const BASE = 'http://localhost:3000';

function testAuth(user?: BetterAuthOptions['user']) {
  const auth = betterAuth({
    baseURL: BASE,
    secret: 'test-secret-that-is-long-enough-for-better-auth',
    database: memoryAdapter({ user: [], session: [], account: [], verification: [] }),
    emailAndPassword: { enabled: true },
    user,
    plugins: [admin(), banMarker()],
  });
  return auth;
}

const cookieHeader = (headers: Headers) =>
  headers
    .getSetCookie()
    .map((c) => c.split(';', 1)[0])
    .join('; ');

const cookieOf = (headers: Headers, name: string) =>
  headers
    .getSetCookie()
    .map((c) => c.split(';', 1)[0] ?? '')
    .find((c) => c.startsWith(`${name}=`));

async function signedUp(auth: ReturnType<typeof testAuth>, email: string) {
  const { headers, response } = await auth.api.signUpEmail({
    body: { email, password: 'correct-horse-battery', name: 'Cy' },
    returnHeaders: true,
  });
  return { userId: response.user.id, marker: cookieOf(headers, MARKER) };
}

const banOf = async (auth: ReturnType<typeof testAuth>, cookie?: string) =>
  (await auth.api.signedInBan({ headers: new Headers(cookie ? { cookie } : {}) })).ban;

describe('banMarker', () => {
  it('tells a marked browser that its user is banned', async () => {
    const auth = testAuth();
    const { userId, marker } = await signedUp(auth, 'cy@example.com');
    expect(marker).toBeDefined();
    const ctx = await auth.$context;
    await ctx.internalAdapter.updateUser(userId, {
      banned: true,
      banReason: 'Spam verificato',
      banExpires: new Date('2099-01-01T00:00:00Z'),
    });
    expect(await banOf(auth, marker)).toEqual({
      reason: 'Spam verificato',
      expires: '2099-01-01T00:00:00.000Z',
    });
  });

  it('says nothing without a marker, or with one the server did not sign', async () => {
    const auth = testAuth();
    const { userId } = await signedUp(auth, 'cy@example.com');
    const ctx = await auth.$context;
    await ctx.internalAdapter.updateUser(userId, { banned: true, banReason: 'Spam' });
    expect(await banOf(auth)).toBeNull();
    expect(await banOf(auth, `${MARKER}=${userId}`)).toBeNull();
    expect(await banOf(auth, `${MARKER}=${userId}.forged-signature`)).toBeNull();
  });

  it('keeps the marker 10 minutes once Better Auth drops the dead session cookie', async () => {
    const auth = testAuth();
    const signUp = await auth.api.signUpEmail({
      body: { email: 'cy@example.com', password: 'correct-horse-battery', name: 'Cy' },
      returnHeaders: true,
    });
    const cookie = cookieHeader(signUp.headers);
    await (await auth.$context).internalAdapter.deleteUserSessions(signUp.response.user.id);
    const { headers } = await auth.api.getSession({
      headers: new Headers({ cookie }),
      returnHeaders: true,
    });
    const marker = headers.getSetCookie().find((c) => c.startsWith(`${MARKER}=`));
    expect(marker?.split(';', 1)[0]).toBe(cookieOf(signUp.headers, MARKER));
    expect(marker).toContain('Max-Age=600');
  });

  it('clears the marker on sign-out', async () => {
    const auth = testAuth();
    const signIn = await auth.api.signUpEmail({
      body: { email: 'cy@example.com', password: 'correct-horse-battery', name: 'Cy' },
      returnHeaders: true,
    });
    const cookie = cookieHeader(signIn.headers);
    const { headers } = await auth.api.signOut({
      headers: new Headers({ cookie, origin: BASE }),
      returnHeaders: true,
    });
    expect(cookieOf(headers, MARKER)).toBe(`${MARKER}=`);
  });

  it('is not on the HTTP router', async () => {
    const auth = testAuth();
    expect((await auth.handler(new Request(`${BASE}/api/auth/ok`))).status).toBe(200);
    expect((await auth.handler(new Request(`${BASE}/api/auth/signed-in-ban`))).status).toBe(404);
  });

  it("keeps the admin's marker while the admin impersonates a user", async () => {
    const auth = testAuth();
    const ctx = await auth.$context;
    const { userId: adminId } = await signedUp(auth, 'admin@example.com');
    await ctx.internalAdapter.updateUser(adminId, { role: 'admin' });
    const signIn = await auth.api.signInEmail({
      body: { email: 'admin@example.com', password: 'correct-horse-battery' },
      returnHeaders: true,
    });
    const { userId } = await signedUp(auth, 'cy@example.com');
    const { headers } = await auth.api.impersonateUser({
      body: { userId },
      headers: new Headers({ cookie: cookieHeader(signIn.headers), origin: BASE }),
      returnHeaders: true,
    });
    expect(cookieOf(headers, 'better-auth.session_token')).toBeDefined();
    expect(cookieOf(headers, MARKER)).toBeUndefined();
  });

  it('keeps the marker when a delete is refused', async () => {
    const auth = testAuth({
      deleteUser: {
        enabled: true,
        beforeDelete: async () => {
          throw new APIError('FORBIDDEN', { message: 'refused' });
        },
      },
    });
    const signUp = await auth.api.signUpEmail({
      body: { email: 'cy@example.com', password: 'correct-horse-battery', name: 'Cy' },
      returnHeaders: true,
    });
    const response = await auth.handler(
      new Request(`${BASE}/api/auth/delete-user`, {
        method: 'POST',
        headers: {
          cookie: cookieHeader(signUp.headers),
          origin: BASE,
          'content-type': 'application/json',
        },
        body: JSON.stringify({ password: 'correct-horse-battery' }),
      }),
    );
    expect(response.status).toBe(403);
    expect(cookieOf(response.headers, MARKER)).toBeUndefined();
  });
});
