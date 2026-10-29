import { redirect } from 'next/navigation';
import { OnboardingWizard } from '@/components/onboarding/onboarding-wizard';
import { getSession } from '@/lib/auth-token';
import { getOnboarding } from '@/lib/onboarding/server';

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: Promise<{ redo?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect('/auth/sign-in');
  const redo = (await searchParams).redo === '1';
  const state = await getOnboarding();
  // Done, skipped, or an existing user: nothing to do here. An API error also lands
  // in the app, the same fail-open as the gate in app/(app)/layout.tsx.
  if (!state.success || (!redo && !state.data.needsOnboarding)) redirect('/');
  return <OnboardingWizard initial={state.data} email={session.user.email} redo={redo} />;
}
