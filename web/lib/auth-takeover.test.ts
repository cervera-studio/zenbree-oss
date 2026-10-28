import { betterAuth } from 'better-auth';
import { memoryAdapter } from 'better-auth/adapters/memory';
import { createAuthMiddleware } from 'better-auth/api';
import type { Pool } from 'pg';
import { describe, expect, it } from 'vite-plus/test';
import {
  dropUnverifiedLinks,
  MICROSOFT_UNVERIFIED,
  refuseUnverifiedMicrosoftUser,
  sendToSetPassword,
  wipeBeforeFirstVerification,
} from './auth-takeover';

const ORIGIN = 'http://localhost:3000';

// The two deletes of wipeBeforeFirstVerification, run on the memory adapter's tables instead of Postgres.
const memoryPool = (db: Record<string, Record<string, unknown>[]>) =>
  ({
    query: async (sql: string, [userId]: string[]) => {
      const table = /DELETE FROM (\w+)/.exec(sql)?.[1] ?? '';
      db[table] = (db[table] ?? []).filter((row) => row.userId !== userId);
    },
  }) as unknown as Pool;

// The same hooks as lib/auth.ts, on the memory adapter.
const harness = () => {
  const links: string[] = [];
  const db: Record<'user' | 'session' | 'account' | 'verification', Record<string, unknown>[]> = {
    user: [],
    session: [],
    account: [],
    verification: [],
  };
  const auth = betterAuth({
    baseURL: ORIGIN,
    secret: 'test-secret-test-secret-test-secret-1',
    database: memoryAdapter(db),
    emailAndPassword: { enabled: true, requireEmailVerification: true },
    emailVerification: {
      sendOnSignUp: true,
      beforeEmailVerification: wipeBeforeFirstVerification(memoryPool(db)),
      sendVerificationEmail: async ({ url }) => {
        links.push(url);
      },
    },
    hooks: {
      after: createAuthMiddleware(async (ctx) => {
        if (ctx.path === '/verify-email') return sendToSetPassword(ctx, ORIGIN);
      }),
    },
  });
  const signUp = (password: string) =>
    auth.api.signUpEmail({ body: { name: 'Anna', email: 'anna@acme.test', password } });
  const signIn = (password: string) =>
    auth.api.signInEmail({ body: { email: 'anna@acme.test', password } }).then(
      () => true,
      () => false,
    );
  const clickLink = (link = links.at(-1) ?? '') => auth.handler(new Request(link));
  const userId = () => String(db.user[0]?.id);
  return { db, signUp, signIn, clickLink, auth, userId, links };
};

const resetToken = (res: Response) => {
  const location = new URL(res.headers.get('location') ?? '', ORIGIN);
  expect(location.pathname).toBe('/auth/reset-password');
  return location.searchParams.get('token') ?? '';
};

describe('the first verification (#979, #980)', () => {
  it('wipes a squatter password, OAuth link and session, and sets the inbox owner password', async () => {
    const { db, signUp, signIn, clickLink, auth, userId } = harness();
    await signUp('squatter-pass-1');
    db.account.push({
      id: 'ms',
      userId: userId(),
      providerId: 'microsoft',
      accountId: 'attacker',
    });
    db.session.push({ id: 's1', userId: userId(), token: 'attacker-token' });

    const res = await clickLink();
    expect(res.status).toBe(302);
    const token = resetToken(res);
    expect(db.account).toEqual([]);
    expect(db.session).toEqual([]);
    expect(await signIn('squatter-pass-1')).toBe(false);

    await auth.api.resetPassword({ body: { token, newPassword: 'owner-pass-2222' } });
    expect(await signIn('owner-pass-2222')).toBe(true);
  });

  it('is not changed by a second sign-up for the address', async () => {
    const { signUp, signIn, clickLink, auth, links } = harness();
    await signUp('first-pass-1');
    await signUp('squatter-pass-1');
    expect(links).toHaveLength(1); // a duplicate sign-up sends nothing and changes nothing
    const token = resetToken(await clickLink());
    await auth.api.resetPassword({ body: { token, newPassword: 'owner-pass-2222' } });
    expect(await signIn('first-pass-1')).toBe(false);
    expect(await signIn('squatter-pass-1')).toBe(false);
    expect(await signIn('owner-pass-2222')).toBe(true);
  });

  it('leaves a verified user alone when the link is clicked again', async () => {
    const { signUp, signIn, clickLink, auth, links } = harness();
    await signUp('first-pass-1');
    const link = links[0] ?? '';
    await auth.api.resetPassword({
      body: { token: resetToken(await clickLink(link)), newPassword: 'owner-pass-2222' },
    });
    const again = await clickLink(link);
    expect(again.headers.get('location') ?? '').not.toContain('reset-password');
    expect(await signIn('owner-pass-2222')).toBe(true);
  });
});

describe('the set-password redirect', () => {
  const signUpAndClick = async (callbackURL?: string) => {
    const h = harness();
    await h.signUp('first-pass-1');
    const link = new URL(h.links[0] ?? '');
    if (callbackURL) link.searchParams.set('callbackURL', callbackURL);
    else link.searchParams.delete('callbackURL');
    return new URL((await h.clickLink(link.href)).headers.get('location') ?? '', ORIGIN);
  };

  it('marks a new account and carries an invitation link through', async () => {
    const to = await signUpAndClick('/invite/abc');
    expect(to.searchParams.get('new')).toBe('1');
    expect(to.searchParams.get('callbackUrl')).toBe('/invite/abc');
  });

  it('carries no callback for the default page, and none that leaves the site', async () => {
    expect((await signUpAndClick()).searchParams.has('callbackUrl')).toBe(false);
    expect((await signUpAndClick('https://evil.test/')).searchParams.has('callbackUrl')).toBe(
      false,
    );
  });
});

describe('dropUnverifiedLinks (#980)', () => {
  it('deletes the non-credential accounts and verifies the address', async () => {
    const queries: [string, unknown[]][] = [];
    const pool = { query: async (sql: string, args: unknown[]) => void queries.push([sql, args]) };
    await dropUnverifiedLinks(pool as unknown as Pool, 'u1');
    expect(queries.map(([sql]) => sql)).toEqual([
      expect.stringMatching(/DELETE FROM account .*"providerId" <> 'credential'/),
      expect.stringMatching(/UPDATE "user" SET "emailVerified" = true/),
    ]);
    expect(queries.map(([, args]) => args)).toEqual([['u1'], ['u1']]);
  });
});

describe('refuseUnverifiedMicrosoftUser (#980)', () => {
  const ctx = (provider: string, where: 'params' | 'body' = 'params') =>
    (where === 'params' ? { params: { id: provider } } : { body: { provider } }) as never;

  it.each(['params', 'body'] as const)('refuses a new unverified Microsoft user (%s)', (where) => {
    expect(() =>
      refuseUnverifiedMicrosoftUser({ emailVerified: false }, ctx('microsoft', where)),
    ).toThrowError(
      expect.objectContaining({ body: expect.objectContaining({ code: MICROSOFT_UNVERIFIED }) }),
    );
  });

  it('lets through a verified Microsoft user, another provider and an email sign-up', () => {
    expect(() =>
      refuseUnverifiedMicrosoftUser({ emailVerified: true }, ctx('microsoft')),
    ).not.toThrow();
    expect(() =>
      refuseUnverifiedMicrosoftUser({ emailVerified: false }, ctx('google')),
    ).not.toThrow();
    expect(() => refuseUnverifiedMicrosoftUser({ emailVerified: false }, null)).not.toThrow();
  });
});
