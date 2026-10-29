import 'server-only';

import { z } from 'zod';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';

const keySchema = z.looseObject({
  id: z.string(),
  label: z.string(),
  last4: z.string().nullable(),
  createdAt: z.string(),
  revokedAt: z.string().nullable(),
});

const keysSchema = z.looseObject({ keys: z.array(keySchema) });
const issuedKeySchema = keySchema.extend({ key: z.string() });

export type ApiKey = z.infer<typeof keySchema>;
export type IssuedApiKey = z.infer<typeof issuedKeySchema>;

const BASE = '/v1/me/api-keys';

export async function getApiKeys() {
  return httpClient.get(BASE, { token: await getToken(), schema: keysSchema });
}

export async function issueApiKey(label: string) {
  return httpClient.post(BASE, { label }, { token: await getToken(), schema: issuedKeySchema });
}

export async function revokeApiKey(id: string) {
  return httpClient.delete(`${BASE}/${encodeURIComponent(id)}`, {
    token: await getToken(),
    schema: keySchema,
  });
}
