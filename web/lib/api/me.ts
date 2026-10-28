import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';

const meSchema = z.looseObject({
  creditsRemaining: z.number().nullable(), // the active organization's wallet; null until its row exists
  planCredits: z.number().nullable(), // what its live plan grants each period (#434); null without a plan
  planCreditsLeft: z.number().nullable(), // the unused part of this period's allowance; null without a plan
  planCreditsResetAt: z.string().nullable(), // when it is removed: the plan's period end
  purchasedCredits: z.number().nullable(), // the rest of the balance, which never expires
}) satisfies z.ZodType<
  Pick<
    Camel<Schema<'Me'>>,
    | 'creditsRemaining'
    | 'planCredits'
    | 'planCreditsLeft'
    | 'planCreditsResetAt'
    | 'purchasedCredits'
  >
>;

export async function getMe() {
  return httpClient.get('/v1/me', { token: await getToken(), schema: meSchema });
}
