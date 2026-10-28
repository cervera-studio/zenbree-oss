import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { createdSchema, deliveriesSchema, listSchema, testSchema } from './schemas';

const BASE = '/v1/integrations/webhooks';
// The route waits for the worker up to its 45 s deadline: a shorter wait would report a failure for a
// delivery that goes out.
const TEST_TIMEOUT_MS = 55_000;

export async function getWebhooks() {
  return httpClient.get(BASE, { token: await getToken(), schema: listSchema });
}

export async function createWebhook(input: { url: string; label: string; eventNames: string[] }) {
  return httpClient.post(BASE, input, { token: await getToken(), schema: createdSchema });
}

export async function deleteWebhook(id: string) {
  return httpClient.delete(`${BASE}/${encodeURIComponent(id)}`, { token: await getToken() });
}

export async function testWebhook(id: string) {
  return httpClient.post(`${BASE}/${encodeURIComponent(id)}/test`, undefined, {
    token: await getToken(),
    schema: testSchema,
    timeoutMs: TEST_TIMEOUT_MS,
  });
}

export async function getDeliveries(id: string) {
  return httpClient.get(`${BASE}/${encodeURIComponent(id)}/deliveries`, {
    token: await getToken(),
    schema: deliveriesSchema,
  });
}
