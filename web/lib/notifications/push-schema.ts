import { z } from 'zod';

/** `GET /v1/notifications/push`, camelCased. `publicKey` is null when the server has no VAPID keys. */
export const pushConfigSchema = z.object({
  publicKey: z.string().nullable(),
  subscribed: z.array(z.string()),
});
export type PushConfig = z.infer<typeof pushConfigSchema>;
