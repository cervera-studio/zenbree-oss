import { randomUUID } from 'node:crypto';
import type { BetterAuthPlugin } from 'better-auth';
import { APIError, createAuthMiddleware, getSessionFromCtx } from 'better-auth/api';
import type { OrganizationOptions } from 'better-auth/plugins';
import type { Pool } from 'pg';
import { requestLocale, storedLocale } from '@/i18n/config';
import { sendInviteEmail } from './email-layout';
import { founderOf } from './founder';

const pinRole = (role: string) => {
  if (role !== 'admin' && role !== 'member') {
    throw new APIError('BAD_REQUEST', { message: 'The role is admin or member' });
  }
};

const founder = () =>
  new APIError('BAD_REQUEST', {
    code: 'FOUNDER',
    message: 'The user whose organization this is stays its admin',
  });

/** Teams a user can create besides their own organization (#676). Counted by founder, so leaving or being
 * removed from one frees nothing; invites are not counted. */
export const TEAMS_PER_USER = 4;

// default_organization() takes a user's oldest membership, and their own organization's row is dated by the
// database (now()). Better Auth dates the rows it writes with this process's clock: a few milliseconds behind
// the database's, a team made right after sign-up sorted first and became the user's default. One clock dates
// them all.
const joinedNow = (pool: Pool, organizationId: string, userId: string) =>
  pool.query(
    'UPDATE member SET "createdAt" = now() WHERE "organizationId" = $1 AND "userId" = $2',
    [organizationId, userId],
  );

// ponytail: count then insert, so concurrent creates can pass the limit by a few; a lock if that gets abused.
async function reachedTeamLimit(pool: Pool, userId: string) {
  const { rows } = await pool.query<{ n: number }>(
    "SELECT count(*)::int AS n FROM organization WHERE split_part(slug, '/', 1) = $1 AND slug <> $1",
    [userId],
  );
  return (rows[0]?.n ?? 0) >= TEAMS_PER_USER;
}

// No UI leaves an organization, and leaving fires no hook: the user's other sessions would stay on it.
export const TEAM_DISABLED_PATHS = ['/organization/leave'];

export async function inviteeLocale(pool: Pool, email: string, request: Request | undefined) {
  const { rows } = await pool.query<{ locale: string }>(
    'SELECT locale FROM "user" WHERE lower(email) = lower($1)',
    [email],
  );
  return rows[0] ? storedLocale(rows[0].locale) : requestLocale(request?.headers);
}

// An invitation carries the authority of whoever sent it: it dies with their membership, and an admin invitation
// with their admin role (#982). Better Auth's accept reads neither, so this guards it and the hooks tidy the list.
const cancelInvitationsOf = (
  pool: Pool,
  organizationId: string,
  userId: string,
  onlyAdmin: boolean,
) =>
  pool.query(
    `UPDATE invitation SET status = 'canceled'
     WHERE "organizationId" = $1 AND "inviterId" = $2 AND status = 'pending' AND ($3::boolean = false OR role = 'admin')`,
    [organizationId, userId, onlyAdmin],
  );

/** The organization plugin's config (#308): admins invite, change roles and remove; members use the rest.
 * creatorRole 'admin' also makes Better Auth refuse to remove or demote the last admin. */
export const teamOptions = (
  pool: Pool,
  baseUrl: string,
  releaseMember: (organizationId: string, userId: string) => Promise<void>,
) =>
  ({
    creatorRole: 'admin',
    allowUserToCreateOrganization: true,
    organizationLimit: (user) => reachedTeamLimit(pool, user.id),
    disableOrganizationDeletion: true,
    cancelPendingInvitationsOnReInvite: true,
    // Explicit: with UUID ids Better Auth's default is off. Accept, decline and reading an invitation also
    // require the session's email to be the invited one (Better Auth checks that itself).
    requireEmailVerificationOnInvitation: true,
    sendInvitationEmail: async ({ id, email, organization, inviter }, request) =>
      sendInviteEmail(
        email,
        inviter.user.email,
        organization.name,
        `${baseUrl}/invite/${id}`,
        await inviteeLocale(pool, email, request),
      ),
    organizationHooks: {
      // #676: a team the user creates. The slug is ours, never the caller's: it names the founder (founderOf),
      // and slug = a user's id marks their own organization (default_organization, own_organization).
      beforeCreateOrganization: async ({ organization, user }) => {
        const name = organization.name?.trim() ?? '';
        if (!name || name.length > 120) {
          throw new APIError('BAD_REQUEST', { message: 'The name is 1 to 120 characters' });
        }
        return { data: { name, slug: `${user.id}/${randomUUID()}`, logo: null, metadata: null } };
      },
      // Its own wallet, empty: the signup grant comes once per user, never once per team they create.
      afterCreateOrganization: async ({ organization, user }) => {
        await pool.query(
          'INSERT INTO user_credits (organization_id, user_id, credits_remaining) VALUES ($1, $2, 0) ON CONFLICT DO NOTHING',
          [organization.id, user.id],
        );
        await joinedNow(pool, organization.id, user.id);
      },
      beforeAcceptInvitation: async ({ invitation }) => {
        const { rows } = await pool.query<{ role: string }>(
          `SELECT role FROM member
           WHERE "organizationId" = $1 AND "userId" = $2 AND zb.is_live_member("userId", "organizationId")`,
          [invitation.organizationId, invitation.inviterId],
        );
        if (!rows[0] || (invitation.role === 'admin' && rows[0].role !== 'admin')) {
          throw new APIError('FORBIDDEN', {
            code: 'INVITER_GONE',
            message: 'The person who sent this invitation can no longer grant it',
          });
        }
      },
      afterUpdateMemberRole: async ({ member, previousRole }) => {
        if (previousRole === 'admin' && member.role !== 'admin') {
          await cancelInvitationsOf(pool, member.organizationId, member.userId, true);
        }
      },
      afterAcceptInvitation: async ({ organization, user }) => {
        await joinedNow(pool, organization.id, user.id);
      },
      // Better Auth always accepts its default 'owner' too, above 'admin': only these two exist here.
      beforeCreateInvitation: async ({ invitation }) => pinRole(invitation.role),
      // A user's own organization, or a team they created, always keeps them as its admin: otherwise an invited
      // admin could demote or remove the founder and keep their lists, campaigns, keys and paid credits.
      beforeUpdateMemberRole: async ({ newRole, member, organization }) => {
        pinRole(newRole);
        if (founderOf(organization.slug) === member.userId && newRole !== 'admin') throw founder();
      },
      // #471: the API revokes their senders at the provider and disconnects them; it throws, so the member
      // stays. It cannot run after the removal: the trigger has already cleared the tokens by then.
      beforeRemoveMember: async ({ member, organization }) => {
        if (founderOf(organization.slug) === member.userId) throw founder();
        await releaseMember(organization.id, member.userId);
      },
      // default_organization() recognises a user's own org by slug = user id.
      beforeUpdateOrganization: async ({ organization }) => {
        if (organization.slug !== undefined) {
          throw new APIError('BAD_REQUEST', { message: 'The slug is fixed' });
        }
      },
      // Their open sessions would keep minting tokens for this org: move them to one they still belong to.
      // The API checks membership on every call, so a token minted before this is refused too.
      afterRemoveMember: async ({ member }) => {
        await cancelInvitationsOf(pool, member.organizationId, member.userId, false);
        await pool.query(
          `UPDATE session SET "activeOrganizationId" = default_organization("userId")::text
           WHERE "userId" = $1 AND "activeOrganizationId" = $2`,
          [member.userId, member.organizationId],
        );
      },
    },
  }) satisfies OrganizationOptions;

/** Invitation emails per 24 hours (#323): to one address, and from one organization. */
export const INVITE_LIMITS = { perAddress: 3, perOrganization: 50 } as const;

const throttled = () =>
  new APIError('TOO_MANY_REQUESTS', {
    code: 'INVITE_THROTTLED',
    message: 'Too many invitations: try again tomorrow',
  });

/** Count one invitation email, or refuse it past INVITE_LIMITS. Only an admin's call counts (anyone else's
 * the endpoint refuses anyway), so a member cannot use up the organization's quota. The advisory lock
 * serialises concurrent sends of one organization, so two cannot both pass the last free slot. */
async function claimInviteSend(pool: Pool, userId: string, org: string, email: string) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const admin = await client.query(
      `SELECT 1 FROM member WHERE "userId" = $1 AND "organizationId" = $2 AND role = 'admin'`,
      [userId, org],
    );
    if (!admin.rowCount) {
      await client.query('ROLLBACK');
      return;
    }
    await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [`invite:${org}`]);
    await client.query(
      `DELETE FROM invitation_sends WHERE organization_id = $1 AND sent_at < now() - interval '24 hours'`,
      [org],
    );
    const claimed = await client.query(
      `INSERT INTO invitation_sends (organization_id, email)
       SELECT $1, $2 WHERE (SELECT count(*) FROM invitation_sends WHERE organization_id = $1) < $3
         AND (SELECT count(*) FROM invitation_sends WHERE organization_id = $1 AND email = $2) < $4`,
      [org, email, INVITE_LIMITS.perOrganization, INVITE_LIMITS.perAddress],
    );
    await client.query(claimed.rowCount ? 'COMMIT' : 'ROLLBACK');
    if (!claimed.rowCount) throw throttled();
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}

/** Throttles invite-member, which sends every invitation email: a new invite, a `resend`, and the re-invite
 * that cancels and re-creates one. Server-side and in the database, so every web process shares the count. */
export const inviteThrottle = (pool: Pool) =>
  ({
    id: 'invite-throttle',
    hooks: {
      before: [
        {
          matcher: (ctx) => ctx.path === '/organization/invite-member',
          handler: createAuthMiddleware(async (ctx) => {
            const session = await getSessionFromCtx(ctx);
            const org = ctx.body?.organizationId ?? session?.session.activeOrganizationId;
            const email = typeof ctx.body?.email === 'string' ? ctx.body.email.toLowerCase() : '';
            if (session && org && email) await claimInviteSend(pool, session.user.id, org, email);
          }),
        },
      ],
    },
  }) satisfies BetterAuthPlugin;
