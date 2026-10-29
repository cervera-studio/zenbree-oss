import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';

const stopSchema = z.looseObject({
  id: z.string(),
  email: z.string(),
  reason: z.enum(['human_reply', 'opt_out', 'hard_bounce']),
  status: z.enum(['pending', 'failed', 'confirmed']),
  attempts: z.number(),
  errorCode: z.string().nullable(),
  nextAttemptAt: z.string().nullable(),
  createdAt: z.string(),
  confirmedAt: z.string().nullable(),
  campaignUrl: z.string(),
}) satisfies z.ZodType<InstantlyStop>;
export type InstantlyStop = Camel<Schema<'StopOut'>>;

const stopsSchema = z.looseObject({
  items: z.array(stopSchema),
  pending: z.number(),
  failed: z.number(),
  paused: z.boolean(),
  mayConfirm: z.boolean(),
}) satisfies z.ZodType<InstantlyStops>;
export type InstantlyStops = Camel<Schema<'StopsOut'>>;

const BASE = '/v1/integrations/instantly/stops';

export async function getInstantlyStops() {
  return httpClient.get(BASE, { token: await getToken(), schema: stopsSchema });
}

export async function moveInstantlyStop(id: string, verb: 'retry' | 'confirm') {
  return httpClient.post(`${BASE}/${encodeURIComponent(id)}/${verb}`, undefined, {
    token: await getToken(),
    schema: stopSchema,
  });
}
