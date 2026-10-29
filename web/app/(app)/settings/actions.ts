'use server';

import { cookies, headers } from 'next/headers';
import { z } from 'zod';
import { LOCALE_COOKIE, LOCALES } from '@/i18n/config';
import { validTimeZone } from '@/i18n/time-zone';
import { auth } from '@/lib/auth';
import { getSession } from '@/lib/auth-token';
import { getOnboarding, saveProfile } from '@/lib/onboarding/server';

const localeSchema = z.enum(LOCALES);
const companySchema = z.string().trim().min(1).max(280);

// Same cookie i18n/request.ts reads; setting it re-renders the page in the new language. A signed-in
// user's choice is also saved on them (#795), except under impersonation: that is the admin's choice.
export async function setLocaleAction(locale: string) {
  const parsed = localeSchema.safeParse(locale);
  if (!parsed.success) return;
  (await cookies()).set(LOCALE_COOKIE, parsed.data, {
    path: '/',
    maxAge: 60 * 60 * 24 * 365,
    sameSite: 'lax',
  });
  const session = await getSession();
  if (!session?.user || session.session.impersonatedBy) return;
  try {
    await auth.api.updateUser({ headers: await headers(), body: { locale: parsed.data } });
  } catch {
    // The cookie already switched the page; the saved language catches up on the next choice.
  }
}

export async function setCompanyNameAction(name: string): Promise<boolean> {
  const parsed = companySchema.safeParse(name);
  if (!parsed.success || !(await getSession())?.user) return false;
  const current = await getOnboarding();
  if (!current.success || !current.data.profile) return false;
  const saved = await saveProfile({ ...current.data.profile, companyName: parsed.data });
  return saved.success;
}

/** Saves the profile's time zone; `null` goes back to the browser's. */
export async function setTimeZoneAction(zone: string | null): Promise<boolean> {
  const timeZone = zone === null ? null : validTimeZone(zone);
  if ((zone !== null && timeZone === null) || !(await getSession())?.user) return false;
  try {
    await auth.api.updateUser({ headers: await headers(), body: { timeZone } });
    return true;
  } catch {
    return false;
  }
}
