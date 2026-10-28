import 'server-only';

import { cache } from 'react';
import { z } from 'zod';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { OUTCOMES } from './instantly-campaign';
import { instantlyAccountsSchema, instantlyStatusSchema } from './schemas';

const BASE = '/v1/integrations/instantly';
// A page waits on this read: an Instantly that hangs must not hold the page for the default 10 s.
const CAMPAIGNS_TIMEOUT_MS = 5000;

const campaignsSchema = z.looseObject({
  campaigns: z.array(
    z.looseObject({ id: z.string(), name: z.string(), status: z.number(), maySend: z.boolean() }),
  ),
});

const enrollmentSchema = z.looseObject({
  id: z.string(),
  status: z.enum(['queued', 'running', 'completed', 'cancelled', 'failed']),
  errorCode: z.string().nullable(),
  campaign: z.looseObject({ id: z.string(), name: z.string(), url: z.string() }),
  counts: z.record(z.enum(OUTCOMES), z.number()),
});
export type InstantlyBatch = z.infer<typeof enrollmentSchema>;

export async function getInstantly() {
  return httpClient.get(BASE, { token: await getToken(), schema: instantlyStatusSchema });
}

// The key is forwarded once and dropped: the API validates it, stores it encrypted and never returns it.
export async function connectInstantly(input: { name: string; apiKey: string }) {
  return httpClient.post(`${BASE}/connect`, input, {
    token: await getToken(),
    schema: instantlyStatusSchema,
  });
}

export async function disconnectInstantly() {
  return httpClient.delete(BASE, { token: await getToken() });
}

// One request per render: the Senders section and every mailbox card's overlap tag read the same answer.
export const getInstantlyAccounts = cache(async () =>
  httpClient.get(`${BASE}/accounts`, { token: await getToken(), schema: instantlyAccountsSchema }),
);

/** The campaigns of the active client's Instantly workspace, read from Instantly now. */
export async function getInstantlyCampaigns() {
  return httpClient.get(`${BASE}/campaigns`, {
    token: await getToken(),
    schema: campaignsSchema,
    timeoutMs: CAMPAIGNS_TIMEOUT_MS,
  });
}

export async function getInstantlyBatch(id: string) {
  return httpClient.get(`${BASE}/enrollments/${encodeURIComponent(id)}`, {
    token: await getToken(),
    schema: enrollmentSchema,
  });
}
