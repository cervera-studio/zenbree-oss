// #308 against a real database: the organization plugin with our config, on a database built from api/db.
import { randomUUID } from 'node:crypto';
import { betterAuth } from 'better-auth';
import { organization } from 'better-auth/plugins';
import type { Pool } from 'pg';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vite-plus/test';
import { INVITE_LIMITS, inviteThrottle, TEAM_DISABLED_PATHS, teamOptions } from './team';
import { migratedDatabase } from './test-database';

const invites = vi.hoisted(() => [] as { to: string; url: string }[]);
vi.mock('./email-layout', () => ({
  sendInviteEmail: async (to: string, _inviter: string, _org: string, url: string) => {
    invites.push({ to, url });
  },
}));

const DB_URL = process.env.TEST_DATABASE_URL;
const base = 'http://zenbree.test';

const release = vi.hoisted(() => vi.fn(async (_org: string, _user: string) => {}));

function makeAuth(pool: Pool) {
  return betterAuth({
    baseURL: base,
    secret: 'test-secret-test-secret-test-secret',
    database: pool,
    advanced: { database: { generateId: 'uuid' } },
    // scrypt costs about 0.3 s per sign-up, and each test signs up two or three users: tests hash in plain text.
    emailAndPassword: {
      enabled: true,
      password: { hash: async (p) => p, verify: async ({ hash, password }) => hash === password },
    },
    disabledPaths: TEAM_DISABLED_PATHS,
    plugins: [organization(teamOptions(pool, base, release)), inviteThrottle(pool)],
  });
}

describe.skipIf(!DB_URL)('team invites, members and roles (#308)', () => {
  let pool: Pool;
  let drop: (() => Promise<void>) | undefined;
  let auth: ReturnType<typeof makeAuth>;
  const q = async <T = Record<string, unknown>>(sql: string, params: unknown[] = []) =>
    (await pool.query(sql, params)).rows as T[];

  // A signed-up user in their own organization (as web/lib/auth.ts makes it), with its session cookie.
  async function user(name: string, verified = true) {
    const email = `${name}-${randomUUID().slice(0, 8)}@x.test`;
    const { headers, response } = await auth.api.signUpEmail({
      body: { email, password: 'password-1234', name },
      returnHeaders: true,
    });
    const cookie = headers
      .getSetCookie()
      .map((c) => c.split(';')[0])
      .join('; ');
    const h = new Headers({ cookie });
    if (verified) {
      await q('UPDATE "user" SET "emailVerified" = true WHERE id = $1', [response.user.id]);
    }
    const [row] = await q<{ org: string }>('SELECT default_organization($1) AS org', [
      response.user.id,
    ]);
    const org = row?.org ?? '';
    await auth.api.setActiveOrganization({ body: { organizationId: org }, headers: h });
    return { id: response.user.id, email, org, h };
  }
  const memberOf = async (userId: string, org: string) =>
    (
      await q<{ id: string; role: string }>(
        'SELECT id, role FROM member WHERE "userId" = $1 AND "organizationId" = $2',
        [userId, org],
      )
    )[0];
  const memberRow = async (userId: string, org: string) => {
    const row = await memberOf(userId, org);
    if (!row) throw new Error(`${userId} is not a member of ${org}`);
    return row;
  };
  const activeOrg = async (userId: string) =>
    (
      await q<{ a: string }>(
        'SELECT "activeOrganizationId" AS a FROM session WHERE "userId" = $1',
        [userId],
      )
    )[0]?.a;
  const invite = (from: { h: Headers; org: string }, email: string, role: 'admin' | 'member') =>
    auth.api.createInvitation({ body: { email, role, organizationId: from.org }, headers: from.h });

  beforeAll(async () => {
    ({ pool, drop } = await migratedDatabase(DB_URL ?? ''));
    auth = makeAuth(pool);
  });

  afterAll(async () => {
    await drop?.();
  });

  it('invites by email, and the invitee accepts into the organization as a member', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const sent = await invite(ada, bo.email, 'member');
    expect(invites.at(-1)).toEqual({ to: bo.email, url: `${base}/invite/${sent.id}` });

    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    expect((await memberOf(bo.id, ada.org))?.role).toBe('member');
    expect(await activeOrg(bo.id)).toBe(ada.org);
    // A user in two organizations: their own and the one they joined.
    const orgs = await auth.api.listOrganizations({ headers: bo.h });
    expect(orgs.map((o) => o.id).sort()).toEqual([ada.org, bo.org].sort());
  });

  it('declines: no membership, the invitation is answered', async () => {
    const ada = await user('ada');
    const cy = await user('cy');
    const sent = await invite(ada, cy.email, 'admin');
    await auth.api.rejectInvitation({ body: { invitationId: sent.id }, headers: cy.h });
    expect(await memberOf(cy.id, ada.org)).toBeUndefined();
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: cy.h }),
    ).rejects.toThrow();
  });

  it('refuses an expired invitation', async () => {
    const ada = await user('ada');
    const di = await user('di');
    const sent = await invite(ada, di.email, 'member');
    await q(`UPDATE invitation SET "expiresAt" = now() - interval '1 minute' WHERE id = $1`, [
      sent.id,
    ]);
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: di.h }),
    ).rejects.toThrow();
    expect(await memberOf(di.id, ada.org)).toBeUndefined();
  });

  it("never grants Better Auth's owner role, and a member grants nothing", async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    await expect(
      auth.api.createInvitation({
        body: { email: bo.email, role: 'owner', organizationId: ada.org },
        headers: ada.h,
      }),
    ).rejects.toThrow(/admin or member/);
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const boMember = await memberRow(bo.id, ada.org);
    const adaMember = await memberRow(ada.id, ada.org);

    await expect(
      auth.api.updateMemberRole({
        body: { memberId: boMember.id, role: 'owner', organizationId: ada.org },
        headers: ada.h,
      }),
    ).rejects.toThrow(/admin or member/);
    await expect(
      auth.api.updateMemberRole({
        body: { memberId: adaMember.id, role: 'owner', organizationId: ada.org },
        headers: ada.h,
      }),
    ).rejects.toThrow();
    // A member can neither promote themselves nor invite.
    await expect(
      auth.api.updateMemberRole({
        body: { memberId: boMember.id, role: 'admin', organizationId: ada.org },
        headers: bo.h,
      }),
    ).rejects.toThrow();
    await expect(invite({ h: bo.h, org: ada.org }, 'eve@x.test', 'member')).rejects.toThrow();
    expect((await memberOf(bo.id, ada.org))?.role).toBe('member');
    expect((await memberOf(ada.id, ada.org))?.role).toBe('admin');
  });

  it('changes roles both ways, but never leaves the organization without an admin', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const boMember = await memberRow(bo.id, ada.org);
    const adaMember = await memberRow(ada.id, ada.org);

    await auth.api.updateMemberRole({
      body: { memberId: boMember.id, role: 'admin', organizationId: ada.org },
      headers: ada.h,
    });
    expect((await memberOf(bo.id, ada.org))?.role).toBe('admin');
    await auth.api.updateMemberRole({
      body: { memberId: boMember.id, role: 'member', organizationId: ada.org },
      headers: ada.h,
    });
    expect((await memberOf(bo.id, ada.org))?.role).toBe('member');
    await expect(
      auth.api.updateMemberRole({
        body: { memberId: adaMember.id, role: 'member', organizationId: ada.org },
        headers: ada.h,
      }),
    ).rejects.toThrow();
    expect((await memberOf(ada.id, ada.org))?.role).toBe('admin');
  });

  it('binds an invitation to its verified recipient', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const eve = await user('eve');
    const sent = await invite(ada, bo.email, 'member');
    for (const answer of [auth.api.acceptInvitation, auth.api.rejectInvitation]) {
      await expect(answer({ body: { invitationId: sent.id }, headers: eve.h })).rejects.toThrow(
        /recipient/i,
      );
    }
    const unverified = await user('cy', false);
    const toCy = await invite(ada, unverified.email, 'member');
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: toCy.id }, headers: unverified.h }),
    ).rejects.toThrow(/verif/i);
    expect(await memberOf(eve.id, ada.org)).toBeUndefined();
    expect(await memberOf(unverified.id, ada.org)).toBeUndefined();
  });

  it('kills the invitations of an inviter who is removed or demoted (#982)', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const cy = await user('cy');
    const eve = await user('eve');
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const boMember = await memberRow(bo.id, ada.org);
    const setRole = (role: 'admin' | 'member') =>
      auth.api.updateMemberRole({
        body: { memberId: boMember.id, role, organizationId: ada.org },
        headers: ada.h,
      });
    const boInTeam = { h: bo.h, org: ada.org };
    const status = async (id: string) =>
      (await q<{ status: string }>('SELECT status FROM invitation WHERE id = $1', [id]))[0]?.status;

    await setRole('admin');
    const asAdmin = await invite(boInTeam, cy.email, 'admin');
    await setRole('member');
    expect(await status(asAdmin.id)).toBe('canceled');
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: asAdmin.id }, headers: cy.h }),
    ).rejects.toThrow();

    await setRole('admin');
    const toEve = await invite(boInTeam, eve.email, 'admin');
    // The inviter's authority is read at accept time, so an invitation the hooks missed is refused too.
    await q(`UPDATE member SET role = 'member' WHERE id = $1`, [boMember.id]);
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: toEve.id }, headers: eve.h }),
    ).rejects.toThrow(/can no longer grant/);
    expect(await memberOf(eve.id, ada.org)).toBeUndefined();

    await q(`UPDATE member SET role = 'admin' WHERE id = $1`, [boMember.id]);
    // A ban is a removal that an unban undoes: the invitation stays pending but cannot be accepted meanwhile.
    const fromBo = await invite(boInTeam, cy.email, 'admin');
    await q(`UPDATE "user" SET banned = true WHERE id = $1`, [bo.id]);
    await expect(
      auth.api.acceptInvitation({ body: { invitationId: fromBo.id }, headers: cy.h }),
    ).rejects.toThrow(/can no longer grant/);
    expect(await status(fromBo.id)).toBe('pending');
    await q(`UPDATE "user" SET banned = false WHERE id = $1`, [bo.id]);
    const last = await invite(boInTeam, cy.email, 'admin');
    await auth.api.removeMember({
      body: { memberIdOrEmail: boMember.id, organizationId: ada.org },
      headers: ada.h,
    });
    expect(await status(last.id)).toBe('canceled');
  });

  it('removes a member and moves their open sessions back to their own organization', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const cy = await user('cy');
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    expect(await activeOrg(bo.id)).toBe(ada.org);
    const toCy = await invite(ada, cy.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: toCy.id }, headers: cy.h });

    const boMember = await memberRow(bo.id, ada.org);
    // A member removes nobody.
    await expect(
      auth.api.removeMember({
        body: { memberIdOrEmail: boMember.id, organizationId: ada.org },
        headers: cy.h,
      }),
    ).rejects.toThrow();
    expect(await memberOf(bo.id, ada.org)).toBeDefined();

    await auth.api.removeMember({
      body: { memberIdOrEmail: boMember.id, organizationId: ada.org },
      headers: ada.h,
    });
    expect(await memberOf(bo.id, ada.org)).toBeUndefined();
    expect(await activeOrg(bo.id)).toBe(bo.org);
  });

  it('releases the senders of a removed member first, and a failed release keeps the member (#471)', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const boMember = await memberRow(bo.id, ada.org);
    const remove = () =>
      auth.api.removeMember({
        body: { memberIdOrEmail: boMember.id, organizationId: ada.org },
        headers: ada.h,
      });

    release.mockClear();
    release.mockRejectedValueOnce(new Error('provider down'));
    await expect(remove()).rejects.toThrow();
    expect(release).toHaveBeenCalledWith(ada.org, bo.id);
    expect(await memberOf(bo.id, ada.org)).toBeDefined();

    await remove();
    expect(release).toHaveBeenCalledTimes(2);
    expect(await memberOf(bo.id, ada.org)).toBeUndefined();
  });

  it('keeps the founder as admin of their own organization, whoever else is admin', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const sent = await invite(ada, bo.email, 'admin');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const adaMember = await memberRow(ada.id, ada.org);
    await expect(
      auth.api.removeMember({
        body: { memberIdOrEmail: adaMember.id, organizationId: ada.org },
        headers: bo.h,
      }),
    ).rejects.toThrow(/stays its admin/);
    expect(release).not.toHaveBeenCalledWith(ada.org, ada.id);
    await expect(
      auth.api.updateMemberRole({
        body: { memberId: adaMember.id, role: 'member', organizationId: ada.org },
        headers: bo.h,
      }),
    ).rejects.toThrow(/stays its admin/);
    expect((await memberOf(ada.id, ada.org))?.role).toBe('admin');
    // The founder can still remove the other admin.
    const boMember = await memberRow(bo.id, ada.org);
    await auth.api.removeMember({
      body: { memberIdOrEmail: boMember.id, organizationId: ada.org },
      headers: ada.h,
    });
    expect(await memberOf(bo.id, ada.org)).toBeUndefined();
  });

  it('has no leave endpoint, which would skip the session move', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const sent = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: sent.id }, headers: bo.h });
    const res = await auth.handler(
      new Request(`${base}/api/auth/organization/leave`, {
        method: 'POST',
        headers: {
          cookie: bo.h.get('cookie') ?? '',
          'content-type': 'application/json',
          origin: base,
        },
        body: JSON.stringify({ organizationId: ada.org }),
      }),
    );
    expect(res.status).toBe(404);
    expect(await memberOf(bo.id, ada.org)).toBeDefined();
  });

  it('keeps the slug, which default_organization() reads, and lets the name change', async () => {
    const ada = await user('ada');
    await expect(
      auth.api.updateOrganization({
        body: { data: { slug: 'taken' }, organizationId: ada.org },
        headers: ada.h,
      }),
    ).rejects.toThrow(/slug/);
    await auth.api.updateOrganization({
      body: { data: { name: 'Agenzia Ada' }, organizationId: ada.org },
      headers: ada.h,
    });
    const [org] = await q<{ name: string; slug: string }>(
      'SELECT name, slug FROM organization WHERE id = $1',
      [ada.org],
    );
    expect(org).toEqual({ name: 'Agenzia Ada', slug: ada.id });
  });

  it('throttles invitation emails per address and per organization (#323)', async () => {
    const ada = await user('ada');
    const bo = await user('bo');
    const to = `eve-${randomUUID().slice(0, 8)}@x.test`;
    const before = invites.length;
    // A new invite, a resend and a re-invite (cancel + create) each send one email.
    await invite(ada, to, 'member');
    await auth.api.createInvitation({
      body: { email: to, role: 'member', organizationId: ada.org, resend: true },
      headers: ada.h,
    });
    await invite(ada, to, 'member');
    expect(invites.length - before).toBe(INVITE_LIMITS.perAddress);
    await expect(invite(ada, to, 'member')).rejects.toThrow(/Too many invitations/);
    expect(invites.length - before).toBe(INVITE_LIMITS.perAddress);
    // Another address still goes; a day later this one does too.
    await invite(ada, `fay-${randomUUID().slice(0, 8)}@x.test`, 'member');
    await q(
      `UPDATE invitation_sends SET sent_at = now() - interval '25 hours' WHERE organization_id = $1`,
      [ada.org],
    );
    await invite(ada, to, 'member');

    // The organization's quota, whatever the address; a member's refused call takes none of it.
    const sent = await q<{ n: number }>(
      "SELECT count(*)::int AS n FROM invitation_sends WHERE organization_id = $1 AND sent_at > now() - interval '1 day'",
      [ada.org],
    );
    const left = INVITE_LIMITS.perOrganization - (sent[0]?.n ?? 0);
    await q(
      `INSERT INTO invitation_sends (organization_id, email)
       SELECT $1, 'x' || g || '@x.test' FROM generate_series(1, $2::int) g`,
      [ada.org, left - 1],
    );
    const accepted = await invite(ada, bo.email, 'member');
    await auth.api.acceptInvitation({ body: { invitationId: accepted.id }, headers: bo.h });
    await expect(invite(ada, `gus-${randomUUID().slice(0, 8)}@x.test`, 'member')).rejects.toThrow(
      /Too many invitations/,
    );
    await expect(invite({ h: bo.h, org: ada.org }, 'hal@x.test', 'member')).rejects.toThrow();
    const counted = await q<{ n: number }>(
      `SELECT count(*)::int AS n FROM invitation_sends WHERE email = 'hal@x.test'`,
    );
    expect(counted[0]?.n).toBe(0);
    // Another organization has its own quota.
    await invite(bo, `ian-${randomUUID().slice(0, 8)}@x.test`, 'member');
  });
});
