import 'server-only';

import { getSession } from '@/lib/auth-token';
import { getRequestOnboarding } from '@/lib/onboarding/server';

/** The line under the settings title, shared by every tab: the client's company and the user's email. */
export async function getSettingsMeta() {
  const [session, onboarding] = await Promise.all([getSession(), getRequestOnboarding()]);
  const company = onboarding.success ? onboarding.data.profile?.companyName : null;
  return [company, session?.user.email].filter(Boolean).join(' · ');
}
