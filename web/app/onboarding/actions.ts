'use server';

import { getLocale } from 'next-intl/server';
import { getSession } from '@/lib/auth-token';
import type { BusinessProfile, Segment } from '@/lib/onboarding/schema';
import { completeOnboarding, proposeSegments, saveProfile } from '@/lib/onboarding/server';
import { AppError, fail } from '@/lib/types';

const unauthenticated = () => fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));

export async function saveProfileAction(profile: BusinessProfile) {
  if (!(await getSession())?.user) return unauthenticated();
  return saveProfile(profile);
}

export async function proposeSegmentsAction() {
  if (!(await getSession())?.user) return unauthenticated();
  return proposeSegments(await getLocale());
}

export async function completeOnboardingAction(segment: Segment | null) {
  if (!(await getSession())?.user) return unauthenticated();
  return completeOnboarding(segment, await getLocale());
}
