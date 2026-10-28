import { z } from 'zod';

const channels = z.object({ email: z.boolean(), browser: z.boolean() });
const browserOnly = z.object({ browser: z.boolean() });

export const DIGEST_FREQUENCIES = ['daily', 'weekly', 'off'] as const;

/** `GET/PUT /v1/notifications/settings`, camelCased. */
export const settingsSchema = z.object({
  events: z.object({
    replyUrgent: channels,
    reply: channels,
    linkedinAccepted: channels,
    approvalNeeded: channels,
    searchDone: channels,
    searchFailed: channels,
    bouncesHigh: browserOnly,
    lowCredits: browserOnly,
  }),
  digest: z.object({
    frequency: z.enum(DIGEST_FREQUENCIES),
    hour: z.number().int().min(0).max(23),
  }),
  dnd: z.boolean(),
});

export type NotificationSettings = z.infer<typeof settingsSchema>;
