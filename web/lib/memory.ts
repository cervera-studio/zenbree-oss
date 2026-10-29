import 'server-only';

import { z } from 'zod';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';

const memorySchema = z.looseObject({
  body: z.string(),
  locked: z.boolean(),
  updatedAt: z.string().nullable(),
  maxChars: z.number(),
  promptMaxChars: z.number(),
  truncated: z.boolean(),
});

export type Memory = z.infer<typeof memorySchema>;

const BASE = '/v1/me/memory';

export async function getMemory() {
  return httpClient.get(BASE, { token: await getToken(), schema: memorySchema });
}

export async function saveMemory(body: string, locked: boolean) {
  return httpClient.put(BASE, { body, locked }, { token: await getToken(), schema: memorySchema });
}
