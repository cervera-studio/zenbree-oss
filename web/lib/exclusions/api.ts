import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import type { ExclusionInput } from './csv';

const exclusionSchema = z.looseObject({
  id: z.string(),
  kind: z.enum(['email', 'domain', 'company_name', 'linkedin_url']),
  value: z.string(),
  origin: z.enum(['profile', 'unsubscribed', 'own_domain', 'manual', 'bounced']),
  createdAt: z.string(),
}) satisfies z.ZodType<Camel<Schema<'Exclusion'>>>;
const listSchema = z.looseObject({
  exclusions: z.array(exclusionSchema),
}) satisfies z.ZodType<Camel<Schema<'Exclusions'>>>;
const addedSchema = z.looseObject({
  added: z.number(),
  invalid: z.array(z.string()),
  invalidCount: z.number(),
}) satisfies z.ZodType<Camel<Schema<'ExclusionsAdded'>>>;

export type Exclusion = Camel<Schema<'Exclusion'>>;

const BASE = '/v1/exclusions';

export async function getExclusions() {
  return httpClient.get(BASE, { token: await getToken(), schema: listSchema });
}

export async function addExclusions(items: ExclusionInput[]) {
  return httpClient.post(BASE, { items }, { token: await getToken(), schema: addedSchema });
}

export async function deleteExclusion(id: string) {
  return httpClient.delete(`${BASE}/${encodeURIComponent(id)}`, { token: await getToken() });
}
