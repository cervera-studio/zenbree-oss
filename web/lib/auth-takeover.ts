import type { GenericEndpointContext } from 'better-auth';
import { generateId } from 'better-auth';
import { APIError } from 'better-auth/api';
import type { Pool } from 'pg';
import { safeCallback } from '../app/auth/sign-in/params';

// #979/#980: the password and the OAuth links of an address nobody has verified were set by whoever got there
// first, not by the owner of the inbox. The first verification therefore wipes them (every credential, every
// social account, every session) and sends the person who proved the inbox to the set-password page with a
// reset token made server-side, the same one Better Auth's own reset email carries. A squatter's password,
// OAuth link or session never outlives the first click on a verification link.

// beforeEmailVerification runs for the plain verification only (not for a change of email), before the user is
// marked verified: the wipe lands first, so no verified user ever holds a squatter's password or link. The
// after hook (which runs for every verification request) then needs to know this one was the first.
const firstVerification = new WeakMap<Request, string>();

export const wipeBeforeFirstVerification =
  (pool: Pool) => async (user: { id: string }, request: Request | undefined) => {
    await pool.query('DELETE FROM account WHERE "userId" = $1', [user.id]);
    await pool.query('DELETE FROM session WHERE "userId" = $1', [user.id]);
    if (request) firstVerification.set(request, user.id);
  };

export async function sendToSetPassword(ctx: GenericEndpointContext, origin: string) {
  const userId = ctx.request ? firstVerification.get(ctx.request) : undefined;
  if (!userId) return;
  const token = generateId(24);
  await ctx.context.internalAdapter.createVerificationValue({
    value: userId,
    identifier: `reset-password:${token}`,
    expiresAt: new Date(Date.now() + 60 * 60 * 1000),
  });
  // The page to land on after the password is set (an invitation link, #323) rides along.
  const params = new URLSearchParams({ token, new: '1' });
  const callbackUrl = safeCallback(ctx.query?.callbackURL);
  if (callbackUrl !== '/') params.set('callbackUrl', callbackUrl);
  throw ctx.redirect(`${origin}/auth/reset-password?${params}`);
}

// A reset proves the inbox as well, so it also verifies the address (the first verification would otherwise
// wipe the password just set) and drops what was linked before.
export async function dropUnverifiedLinks(pool: Pool, userId: string) {
  await pool.query('DELETE FROM account WHERE "userId" = $1 AND "providerId" <> \'credential\'', [
    userId,
  ]);
  await pool.query('UPDATE "user" SET "emailVerified" = true WHERE id = $1', [userId]);
}

// #980: Entra sends no verified-email claim for some accounts (personal ones), so Microsoft can name an address
// its owner never proved. Such a sign-up creates nothing: the person signs up with email first, which verifies the
// address, and the Microsoft sign-in then links to a verified user. Existing users and linking are unchanged.
export const MICROSOFT_UNVERIFIED = 'MICROSOFT_EMAIL_UNVERIFIED';

export function refuseUnverifiedMicrosoftUser(
  user: { emailVerified?: boolean | null },
  ctx: GenericEndpointContext | null,
) {
  const provider = ctx?.params?.id ?? ctx?.body?.provider;
  if (provider !== 'microsoft' || user.emailVerified) return;
  throw new APIError('FORBIDDEN', {
    code: MICROSOFT_UNVERIFIED,
    message: 'Microsoft did not verify this address',
  });
}
