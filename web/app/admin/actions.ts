'use server';

// Every admin mutation. Better Auth's admin endpoints and the API's
// require_admin both re-check the caller's role, so no guard here.
import { refresh } from 'next/cache';
import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import * as api from '@/lib/admin/api';
import { auth } from '@/lib/auth';

export type ActionResult = { ok: true } | { ok: false; error: string };

async function run(fn: () => Promise<unknown>): Promise<ActionResult> {
  try {
    await fn();
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  refresh();
  return { ok: true };
}

export async function setRole(userId: string, role: 'user' | 'admin') {
  return run(async () => auth.api.setRole({ headers: await headers(), body: { userId, role } }));
}

export async function banUser(userId: string, banReason: string) {
  return run(async () =>
    auth.api.banUser({
      headers: await headers(),
      body: { userId, banReason: banReason || undefined },
    }),
  );
}

export async function unbanUser(userId: string) {
  return run(async () => auth.api.unbanUser({ headers: await headers(), body: { userId } }));
}

export async function revokeSessions(userId: string) {
  return run(async () =>
    auth.api.revokeUserSessions({ headers: await headers(), body: { userId } }),
  );
}

// nextCookies() swaps the session cookie for the impersonated one.
export async function impersonate(userId: string): Promise<ActionResult> {
  try {
    await auth.api.impersonateUser({ headers: await headers(), body: { userId } });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  redirect('/');
}

/** On a user's own organization's wallet, or (#327) straight on an organization's. */
export async function adjustCredits(
  target: { user: string } | { organization: string },
  amount: number,
  reason: string,
) {
  const res =
    'user' in target
      ? await api.adjustCredits(target.user, amount, reason)
      : await api.adjustOrganizationCredits(target.organization, amount, reason);
  if (!res.success) return { ok: false as const, error: `${res.error.code}: ${res.error.message}` };
  refresh();
  return { ok: true as const };
}

export async function setSetting(key: string, value: unknown): Promise<ActionResult> {
  const res = await api.putSetting(key, value);
  if (!res.success) return { ok: false, error: `${res.error.code}: ${res.error.message}` };
  refresh();
  return { ok: true };
}

export async function addSetting(
  key: string,
): Promise<{ ok: true } | { ok: false; code: string; error: string }> {
  const res = await api.addSetting(key);
  if (!res.success) {
    return { ok: false, code: res.error.code, error: `${res.error.code}: ${res.error.message}` };
  }
  refresh();
  return { ok: true };
}
