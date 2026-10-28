import 'server-only';

import { cache } from 'react';
import { z } from 'zod';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import {
  type BusinessProfile,
  profileSchema,
  type Segment,
  segmentSchema,
  stateSchema,
} from './schema';

const BASE = '/v1/onboarding';
// One LLM call: well past the 10s default.
const LLM_TIMEOUT_MS = 120_000;

export async function getOnboarding(timeoutMs?: number) {
  return httpClient.get(BASE, { token: await getToken(), timeoutMs, schema: stateSchema });
}

/** The state for the app layout and the pages under it, with the layout's short timeout. Cached so
 *  one request makes one call: React does not merge fetches that carry their own abort signal. */
export const getRequestOnboarding = cache(() => getOnboarding(2500));

export async function saveProfile(profile: BusinessProfile) {
  return httpClient.put(`${BASE}/profile`, profile, {
    token: await getToken(),
    schema: z.looseObject({ profile: profileSchema }),
  });
}

export async function proposeSegments(locale: string) {
  return httpClient.post(
    `${BASE}/segments`,
    { locale },
    {
      token: await getToken(),
      timeoutMs: LLM_TIMEOUT_MS,
      schema: z.looseObject({ segments: z.array(segmentSchema) }),
    },
  );
}

export async function completeOnboarding(segment: Segment | null, locale: string) {
  return httpClient.post(
    `${BASE}/complete`,
    { segment, locale },
    { token: await getToken(), schema: z.looseObject({ sessionId: z.string().nullable() }) },
  );
}
