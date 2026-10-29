import { dash, sentinel } from '@better-auth/infra';
import { betterAuth } from 'better-auth';
import { APIError, createAuthMiddleware, getSessionFromCtx } from 'better-auth/api';
import { nextCookies } from 'better-auth/next-js';
import { admin, jwt, organization } from 'better-auth/plugins';
import { Pool } from 'pg';
import { z } from 'zod';
import { LOCALES, requestLocale, storedLocale } from '@/i18n/config';
import { validTimeZone } from '@/i18n/time-zone';
import { encodeBan } from '../app/auth/sign-in/params';
import { authEmailSender } from './auth-email-throttle';
import { AUTH_IP_ADDRESS } from './auth-ip';
import {
  dropUnverifiedLinks,
  refuseUnverifiedMicrosoftUser,
  sendToSetPassword,
  wipeBeforeFirstVerification,
} from './auth-takeover';
import { banMarker } from './ban-marker';
import { dashGuard } from './dash-guard';
import { signupEnabled } from './signup';
import { grantSignupCredits } from './signup-credits';
import { inviteThrottle, TEAM_DISABLED_PATHS, teamOptions } from './team';

const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const sendAuthEmail = authEmailSender(pool);

const baseUrl = process.env.BETTER_AUTH_URL ?? 'http://localhost:3000';

// The user's organization, created on first use: every user is the admin of a one-person org (#305).
// The SQL function (api/db/*_organizations.sql) also ran the backfill, so both make the same one.
const defaultOrgId = async (userId: string): Promise<string | null> => {
  const { rows } = await pool.query('SELECT default_organization($1) AS id', [userId]);
  return rows[0]?.id ?? null;
};

// #333/#341: before any account delete the API refuses the last admin of a team, cancels the billing of an
// organization the user is the only member of, and releases their senders. Any failure aborts the delete; a
// database trigger holds both rules when anything skips this. A self-delete runs it from deleteUser.beforeDelete,
// while the session still mints a token (Better Auth drops the sessions before the user row), naming the session's
// user so the API refuses a token for anyone else. An admin's runs from a hook before admin/remove-user, which drops
// the user's sessions and password before the user row too, so a refused delete leaves them able to sign in. That
// hook reads the raw body: it takes only a string id, and the user delete hook refuses any user but that one.
const HTTP_STATUS: Partial<Record<number, ConstructorParameters<typeof APIError>[0]>> = {
  401: 'UNAUTHORIZED',
  403: 'FORBIDDEN',
  409: 'CONFLICT',
};
const SELF_DELETE_PATHS = ['/delete-user', '/delete-user/callback'];
const adminBeforeDelete = (userId: string) =>
  callApi(`/v1/admin/users/${encodeURIComponent(userId)}/before-delete`);

const callApi = async (path: string, body?: Record<string, string>) => {
  const [{ httpClient }, { getToken }] = await Promise.all([
    import('./http-client'),
    import('./auth-token'),
  ]);
  const token = await getToken();
  if (!token)
    throw new APIError('UNAUTHORIZED', { code: 'AUTH.TOKEN_MISSING', message: 'No session' });
  const result = await httpClient.post(path, body, { token });
  if (!result.success) {
    const { code, message, status } = result.error;
    throw new APIError(HTTP_STATUS[status] ?? 'BAD_GATEWAY', { code, message });
  }
};

// The callbacks' `user` type leaves out additionalFields, so the column is read off the object.
const userLocale = (user: object, request: Request | undefined) =>
  'locale' in user && user.locale != null
    ? storedLocale(user.locale)
    : requestLocale(request?.headers);

export const auth = betterAuth({
  baseURL: baseUrl,
  database: pool,
  advanced: { database: { generateId: 'uuid' }, ipAddress: AUTH_IP_ADDRESS },
  disabledPaths: TEAM_DISABLED_PATHS,
  emailAndPassword: {
    enabled: true,
    disableSignUp: !signupEnabled(),
    requireEmailVerification: true,
    revokeSessionsOnPasswordReset: true,
    // A reset proves the mailbox: an unverified user's OAuth links (#980) are dropped with the old sessions.
    onPasswordReset: async ({ user }) => {
      if (!user.emailVerified) await dropUnverifiedLinks(pool, user.id);
    },
    sendResetPassword: ({ user, url }, request) =>
      sendAuthEmail('reset-password', user.email, user.name, url, userLocale(user, request)),
  },
  emailVerification: {
    sendOnSignUp: true,
    // #979/#980: the first verification wipes the password and links, then sets a password (hooks.after).
    beforeEmailVerification: wipeBeforeFirstVerification(pool),
    sendVerificationEmail: ({ user, url }, request) =>
      sendAuthEmail('verify-email', user.email, user.name, url, userLocale(user, request)),
  },
  // #341: no password asked; Better Auth refuses (SESSION_EXPIRED) a session older than freshAge (1 day).
  // The rows go as for an admin's delete (#333): the organization keeps what the user created in it.
  user: {
    additionalFields: {
      // Written by setLocaleAction (an explicit choice), never by opening the app (#795).
      locale: {
        type: 'string',
        required: false,
        defaultValue: 'en',
        validator: { input: z.enum(LOCALES) },
      },
      // The IANA zone the user chose in the profile (#348); null follows the browser's.
      timeZone: {
        type: 'string',
        required: false,
        validator: {
          input: z
            .string()
            .transform((zone, ctx) => {
              const canonical = validTimeZone(zone);
              if (canonical === null)
                ctx.addIssue({ code: 'custom', message: 'Unknown time zone' });
              return canonical ?? zone;
            })
            .nullable(),
        },
      },
    },
    // Two steps: the CURRENT address approves, then the new one verifies. A session alone never moves the account.
    changeEmail: {
      enabled: true,
      sendChangeEmailConfirmation: ({ user, newEmail, url }, request) =>
        sendAuthEmail(
          'change-email',
          user.email,
          user.name,
          url,
          userLocale(user, request),
          newEmail,
        ),
    },
    deleteUser: {
      enabled: true,
      beforeDelete: (user) => callApi('/v1/me/before-delete', { userId: user.id }),
    },
  },
  socialProviders: {
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID ?? '',
      clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? '',
      disableSignUp: !signupEnabled(),
    },
    microsoft: {
      clientId: process.env.MICROSOFT_CLIENT_ID ?? '',
      clientSecret: process.env.MICROSOFT_CLIENT_SECRET ?? '',
      tenantId: process.env.MICROSOFT_TENANT_ID ?? 'common',
      disableSignUp: !signupEnabled(),
    },
  },
  hooks: {
    before: createAuthMiddleware(async (ctx) => {
      if (ctx.path !== '/admin/remove-user') return;
      // The endpoint would coerce anything into a string id (z.coerce.string): only a string is checked, so the
      // user delete hook below can hold the delete to that same id.
      const userId = ctx.body?.userId;
      if (typeof userId !== 'string') {
        throw new APIError('BAD_REQUEST', {
          code: 'INVALID_USER_ID',
          message: 'userId must be a string',
        });
      }
      // Better Auth refuses removing yourself (YOU_CANNOT_REMOVE_YOURSELF) after this hook: nothing to run first.
      if ((await getSessionFromCtx(ctx))?.user.id === userId) return;
      await adminBeforeDelete(userId);
    }),
    // #389: a ban stops the user's running jobs (the API acts only while the ban is on, so a refused ban changes
    // nothing). Campaigns, scheduled searches and webhooks need no call: the API skips a banned creator's when they
    // act, so an unban or the ban's expiry resumes them.
    // ponytail: a ban written elsewhere (SQL, the dash plugin) cancels no job; the job's next credit hold is refused,
    // so it spends at most the hold it has. Move the cancel to the worker if those paths get used.
    after: createAuthMiddleware(async (ctx) => {
      if (ctx.path === '/verify-email') return sendToSetPassword(ctx, baseUrl);
      if (ctx.path !== '/admin/ban-user') return;
      await callApi(`/v1/admin/users/${encodeURIComponent(String(ctx.body?.userId))}/after-ban`);
    }),
  },
  databaseHooks: {
    user: {
      create: {
        before: async (user, ctx) => {
          refuseUnverifiedMicrosoftUser(user, ctx);
          return {
            data: { ...user, locale: requestLocale(ctx?.request?.headers ?? ctx?.headers) },
          };
        },
        // The new user's organization and its credit wallet (#306).
        after: async (user) => {
          await grantSignupCredits(pool, await defaultOrgId(user.id), user.id);
        },
      },
      // ctx.path is the endpoint's declared path, never the raw URL. A self-delete ran beforeDelete; admin/remove-user
      // ran the hook above for the string id in its body, and only that user may go. Any other path that reaches
      // this hook is refused. The dash() plugin's /dash/delete-user and /dash/delete-many-users call the adapter
      // directly and never reach it: only the database trigger (guard_account_delete) holds the rules there.
      delete: {
        before: async (user, ctx) => {
          if (ctx && SELF_DELETE_PATHS.includes(ctx.path)) return;
          if (ctx?.path === '/admin/remove-user' && ctx.body?.userId === user.id) return;
          throw new APIError('FORBIDDEN', {
            code: 'ACCOUNT.DELETE_PATH',
            message: 'Not a delete path',
          });
        },
      },
    },
    session: {
      create: {
        before: async (session) => ({
          data: { ...session, activeOrganizationId: await defaultOrgId(session.userId) },
        }),
      },
    },
  },
  plugins: [
    dash(),
    dashGuard(),
    sentinel(),
    admin({ bannedUserMessage: encodeBan }),
    banMarker(),
    organization(
      teamOptions(pool, baseUrl, (organizationId, userId) =>
        callApi(`/v1/integrations/senders/members/${encodeURIComponent(userId)}/release`, {
          organizationId,
        }),
      ),
    ),
    inviteThrottle(pool),
    // EdDSA (default); the API verifies against /api/auth/jwks.
    jwt({
      jwt: {
        issuer: baseUrl,
        audience: baseUrl,
        // org: the API scopes every shared row by it. A session from before #305 has no active org yet.
        definePayload: async ({ user, session }) => ({
          email: user.email,
          role: user.role,
          org: session.activeOrganizationId ?? (await defaultOrgId(user.id)),
        }),
      },
    }),
    nextCookies(), // must stay last
  ],
});
