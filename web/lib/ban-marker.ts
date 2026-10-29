import type { BetterAuthPlugin } from 'better-auth';
import { createAuthEndpoint, createAuthMiddleware, isAPIError } from 'better-auth/api';
import { expireCookie, parseSetCookieHeader } from 'better-auth/cookies';
import type { UserWithRole } from 'better-auth/plugins';
import { activeBan, type BanInfo } from '../app/auth/sign-in/params';

// #762: a ban deletes the user's sessions, so a browser that was signed in can no longer say whose
// session it lost. This signed cookie names the last user who signed in on it; the sign-in page asks
// whether that user is banned instead of showing "session expired". Only the server sets it, after a
// real sign-in, so a URL or a stranger cannot make the page show anyone's ban.
const MARKER = 'signed_in_user';
const CLEAR_PATHS = ['/sign-out', '/delete-user', '/delete-user/callback'];
// Once Better Auth drops a dead session cookie, the marker lives this much longer: enough for the
// app to land on the sign-in page, too short to show the ban to the next person on the browser.
const AFTER_SESSION_DIES = 10 * 60;

type Ctx = Parameters<Parameters<typeof createAuthMiddleware>[0]>[0];

// The marker only matters while a session cookie could still be sent.
const marker = (ctx: Ctx) =>
  ctx.context.createAuthCookie(MARKER, { maxAge: ctx.context.sessionConfig.expiresIn });

export const banMarker = () =>
  ({
    id: 'ban-marker',
    endpoints: {
      signedInBan: createAuthEndpoint.serverOnly({ method: 'GET' }, async (ctx) => {
        const userId = await ctx.getSignedCookie(marker(ctx).name, ctx.context.secret);
        // The row has the admin plugin's ban columns; the core User type leaves them out.
        const user = userId
          ? ((await ctx.context.internalAdapter.findUserById(userId)) as UserWithRole | null)
          : null;
        const ban: BanInfo | null = user ? activeBan(user, new Date()) : null;
        return ctx.json({ ban });
      }),
    },
    hooks: {
      after: [
        {
          matcher: () => true,
          handler: createAuthMiddleware(async (ctx) => {
            const created = ctx.context.newSession;
            // An admin impersonating keeps their own marker: the target's ban is not theirs to see.
            if (!created || created.session.impersonatedBy) return;
            const { name, attributes } = marker(ctx);
            await ctx.setSignedCookie(name, created.user.id, ctx.context.secret, attributes);
          }),
        },
        {
          matcher: (ctx) => !CLEAR_PATHS.includes(ctx.path ?? ''),
          handler: createAuthMiddleware(async (ctx) => {
            const session = ctx.context.authCookies.sessionToken.name;
            const dropped =
              parseSetCookieHeader(ctx.context.responseHeaders?.get('set-cookie') ?? '').get(
                session,
              )?.['max-age'] === 0;
            if (ctx.context.newSession || !dropped || !ctx.getCookie(session)) return;
            const { name, attributes } = marker(ctx);
            const userId = await ctx.getSignedCookie(name, ctx.context.secret);
            if (!userId) return;
            await ctx.setSignedCookie(name, userId, ctx.context.secret, {
              ...attributes,
              maxAge: AFTER_SESSION_DIES,
            });
          }),
        },
        {
          matcher: (ctx) => CLEAR_PATHS.includes(ctx.path ?? ''),
          handler: createAuthMiddleware(async (ctx) => {
            if (!isAPIError(ctx.context.returned)) expireCookie(ctx, marker(ctx));
          }),
        },
      ],
    },
  }) satisfies BetterAuthPlugin;
