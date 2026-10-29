import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { type PushConfig, pushConfigSchema } from './push-schema';

/** The server's push state; a failed read counts as push off, so a page never breaks on it. */
export async function getPushConfig(): Promise<PushConfig> {
  const res = await httpClient.get('/v1/notifications/push', {
    token: await getToken(),
    schema: pushConfigSchema,
  });
  return res.success ? res.data : { publicKey: null, subscribed: [] };
}
