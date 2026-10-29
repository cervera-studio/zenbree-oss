import { dash } from '@better-auth/infra';
import { betterAuth } from 'better-auth';
import { createAuthEndpoint } from 'better-auth/api';
import { describe, expect, it } from 'vite-plus/test';
import { dashGuard } from './dash-guard';

// A route a later Infra version might add.
const future = {
  id: 'future-dash-route',
  endpoints: {
    future: createAuthEndpoint('/dash/future', { method: 'GET' }, async (ctx) =>
      ctx.json({ ok: true }),
    ),
  },
};

const auth = (guarded: boolean) =>
  betterAuth({
    baseURL: 'http://zenbree.test',
    secret: 'test-secret-test-secret-test-secret',
    plugins: [dash({ apiKey: 'test-key' }), future, ...(guarded ? [dashGuard()] : [])],
  });

const call = (a: ReturnType<typeof auth>, method: string, path: string, authorization?: string) =>
  a.handler(
    new Request(`http://zenbree.test/api/auth${path}`, {
      method,
      headers: { 'content-type': 'application/json', ...(authorization && { authorization }) },
      ...(method === 'POST' && {
        body: JSON.stringify({ action: 'findOne', model: 'user', where: [] }),
      }),
    }),
  );

const REFUSED = [
  ['POST', '/dash/execute-adapter'],
  ['GET', '/dash/impersonate-user?impersonation_token=x'],
  ['POST', '/dash/set-password'],
  ['POST', '/dash/organization/delete'],
  ['POST', '/dash/organization/delete-many'],
  ['POST', '/dash/organization/add-member'],
  ['POST', '/dash/organization/update-member-role'],
  ['POST', '/dash/organization/remove-member'],
  ['POST', '/dash/delete-user'],
  ['POST', '/dash/ban-user'],
  ['POST', '/dash/organization/00000000-0000-0000-0000-000000000000/sso-provider/create'],
] as const;

const READS = [
  '/dash/list-users',
  '/dash/organization/00000000-0000-0000-0000-000000000000/members',
] as const;

describe('dashGuard (#978)', () => {
  it.each(REFUSED)('refuses %s %s whatever the token says', async (method, path) => {
    for (const token of [undefined, 'Bearer not-a-jwt']) {
      const res = await call(auth(true), method, path, token);
      expect([res.status, (await res.json()).message]).toEqual([404, 'Not found']);
    }
  });

  it('closes a dash route that is not on the list, by default', async () => {
    expect((await call(auth(false), 'GET', '/dash/future')).status).toBe(200);
    const res = await call(auth(true), 'GET', '/dash/future');
    expect([res.status, (await res.json()).message]).toEqual([404, 'Not found']);
  });

  it('refuses what the unguarded plugin serves', async () => {
    // The control: without the guard the same write route reaches its handler or JWT check, never a plain 404.
    expect((await call(auth(false), 'POST', '/dash/execute-adapter')).status).not.toBe(404);
  });

  it.each(READS)('leaves %s to its own JWT check', async (path) => {
    expect((await call(auth(true), 'GET', path)).status).toBe(401);
  });
});
