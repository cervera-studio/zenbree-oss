// #676 against a real database: a user creates a team through the organization plugin with our config.
import { randomUUID } from 'node:crypto';
import { betterAuth } from 'better-auth';
import { organization } from 'better-auth/plugins';
import type { Pool } from 'pg';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vite-plus/test';
import { TEAMS_PER_USER, teamOptions } from './team';
import { migratedDatabase } from './test-database';

const DB_URL = process.env.TEST_DATABASE_URL;
const base = 'http://zenbree.test';

function makeAuth(pool: Pool) {
  return betterAuth({
    baseURL: base,
    secret: 'test-secret-test-secret-test-secret',
    database: pool,
    advanced: { database: { generateId: 'uuid' } },
    emailAndPassword: {
      enabled: true,
      password: { hash: async (p) => p, verify: async ({ hash, password }) => hash === password },
    },
    plugins: [organization(teamOptions(pool, base, async () => {}))],
  });
}

describe.skipIf(!DB_URL)('a user creates a team (#676)', () => {
  let pool: Pool;
  let drop: (() => Promise<void>) | undefined;
  let auth: ReturnType<typeof makeAuth>;
  const q = async <T = Record<string, unknown>>(sql: string, params: unknown[] = []) =>
    (await pool.query(sql, params)).rows as T[];

  // A signed-up user with their own organization and its signup wallet, as web/lib/auth.ts makes them.
  async function user() {
    const { headers, response } = await auth.api.signUpEmail({
      body: {
        email: `ada-${randomUUID().slice(0, 8)}@x.test`,
        password: 'password-1234',
        name: 'Ada',
      },
      returnHeaders: true,
    });
    const id = response.user.id;
    await q(
      'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES (default_organization($1), $1, 25)',
      [id],
    );
    const cookie = headers
      .getSetCookie()
      .map((c) => c.split(';')[0])
      .join('; ');
    return { id, h: new Headers({ cookie }) };
  }
  const create = (h: Headers, name: string, slug = randomUUID()) =>
    auth.api.createOrganization({ body: { name, slug }, headers: h });

  beforeAll(async () => {
    ({ pool, drop } = await migratedDatabase(DB_URL ?? ''));
    auth = makeAuth(pool);
  });

  afterAll(async () => {
    await drop?.();
  });

  it('makes the creator its admin and moves them into it, with an empty wallet', async () => {
    const ada = await user();
    const team = await create(ada.h, '  Agenzia Beta ');
    expect(team?.name).toBe('Agenzia Beta');
    const [member] = await q<{ role: string }>(
      'SELECT role FROM member WHERE "userId" = $1 AND "organizationId" = $2',
      [ada.id, team?.id],
    );
    expect(member?.role).toBe('admin');
    const [session] = await q<{ a: string }>(
      'SELECT "activeOrganizationId" AS a FROM session WHERE "userId" = $1',
      [ada.id],
    );
    expect(session?.a).toBe(team?.id);
    // No second signup grant; the signup wallet and the user's own organization stay as they were.
    const wallets = await q<{ org: string; credits: number }>(
      'SELECT organization_id AS org, credits_remaining AS credits FROM user_credits WHERE user_id = $1',
      [ada.id],
    );
    expect(wallets).toContainEqual({ org: team?.id, credits: 0 });
    expect(wallets.reduce((n, w) => n + w.credits, 0)).toBe(25);
    const [own] = await q<{ org: string }>('SELECT default_organization($1) AS org', [ada.id]);
    expect(own?.org).not.toBe(team?.id);
  });

  it("keeps the user's own organization as their default when this server's clock is behind the database's", async () => {
    const ada = await user();
    const [before] = await q<{ org: string }>('SELECT default_organization($1) AS org', [ada.id]);
    // Better Auth dates the membership with this process's clock; the own organization's is the database's.
    vi.useFakeTimers({ toFake: ['Date'], now: Date.now() - 60_000 });
    try {
      const team = await create(ada.h, 'Agenzia Gamma');
      const [after] = await q<{ org: string }>('SELECT default_organization($1) AS org', [ada.id]);
      expect(after?.org).toBe(before?.org);
      expect(after?.org).not.toBe(team?.id);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never takes the caller's slug: a user's id there would make the team pass for theirs", async () => {
    const ada = await user();
    // A user's id with no organization yet: Better Auth only refuses a slug that is taken.
    const unborn = randomUUID();
    const team = await create(ada.h, 'Rubata', unborn);
    expect(team?.slug).not.toBe(unborn);
    expect(team?.slug).toMatch(new RegExp(`^${ada.id}/`));
  });

  it('keeps the creator its admin: an admin they invite can neither demote nor remove them', async () => {
    const ada = await user();
    const bo = await user();
    const team = await create(ada.h, 'Agenzia');
    const id = team?.id ?? '';
    await q(
      `INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES ($1, $2, 'admin', now())`,
      [id, bo.id],
    );
    await auth.api.setActiveOrganization({ body: { organizationId: id }, headers: bo.h });
    const [adaMember] = await q<{ id: string }>(
      'SELECT id FROM member WHERE "userId" = $1 AND "organizationId" = $2',
      [ada.id, id],
    );
    await expect(
      auth.api.updateMemberRole({
        body: { memberId: adaMember?.id ?? '', role: 'member', organizationId: id },
        headers: bo.h,
      }),
    ).rejects.toThrow(/stays its admin/);
    await expect(
      auth.api.removeMember({
        body: { memberIdOrEmail: adaMember?.id ?? '', organizationId: id },
        headers: bo.h,
      }),
    ).rejects.toThrow(/stays its admin/);
  });

  it('refuses a blank name and stops at the limit, which leaving a team does not reset', async () => {
    const ada = await user();
    await expect(create(ada.h, '   ')).rejects.toThrow(/name/);
    // Invites don't count: a membership in someone else's team leaves the limit as it was.
    const bo = await user();
    const theirs = await create(bo.h, 'Di Bo');
    await q(
      `INSERT INTO member ("organizationId", "userId", role, "createdAt") VALUES ($1, $2, 'member', now())`,
      [theirs?.id, ada.id],
    );
    const made = [];
    for (let n = 0; n < TEAMS_PER_USER; n++) made.push(await create(ada.h, `Team ${n}`));
    await expect(create(ada.h, 'One too many')).rejects.toThrow();
    await q('DELETE FROM member WHERE "userId" = $1 AND "organizationId" = $2', [
      ada.id,
      made[0]?.id,
    ]);
    await expect(create(ada.h, 'Still too many')).rejects.toThrow();
  });
});
