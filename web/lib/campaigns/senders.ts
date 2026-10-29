import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { MAILBOX_PROVIDERS } from '@/lib/integrations/schemas';
import { SENDER_REASONS, SENDER_STATES } from '@/lib/integrations/sender-state';

export type CampaignSender = Camel<Schema<'CampaignSender'>>;
export type CampaignSenders = Camel<Schema<'CampaignSenders'>>;

const campaignSendersSchema = z.object({
  senders: z.array(
    z.object({
      id: z.uuid(),
      provider: z.enum(MAILBOX_PROVIDERS),
      accountLabel: z.string().nullable(),
      status: z.enum(['connected', 'error', 'disconnected']),
      picked: z.boolean(),
      state: z.enum(SENDER_STATES),
      reason: z.enum(SENDER_REASONS).nullable(),
      blocker: z.enum(['disconnected', 'needs_reconnect', 'paused']).nullable(),
      resumesAt: z.iso.datetime({ offset: true }).nullable(),
      sentToday: z.number(),
      dailyCap: z.number(),
      warmingUp: z.boolean(),
    }),
  ),
  maxSenders: z.number(),
}) satisfies z.ZodType<CampaignSenders>;

const at = (id: string) => `/v1/campaigns/${encodeURIComponent(id)}/senders`;

export async function getCampaignSenders(id: string) {
  return httpClient.get(at(id), { token: await getToken(), schema: campaignSendersSchema });
}

export async function setCampaignSenders(id: string, senderIds: string[]) {
  return httpClient.put(
    at(id),
    { senderIds },
    { token: await getToken(), schema: campaignSendersSchema },
  );
}
