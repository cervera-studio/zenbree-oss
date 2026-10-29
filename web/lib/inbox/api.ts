import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { replyHoldSchema } from '@/lib/campaigns/mailbox-state';
import { httpClient } from '@/lib/http-client';

export const LABELS = [
  'interested',
  'meeting_request',
  'not_interested',
  'not_now',
  'other',
] as const satisfies readonly Label[];
export type Label = NonNullable<Schema<'InboxMessage'>['label']>;

const messageSchema = z.looseObject({
  id: z.string(),
  channel: z.enum(['email', 'linkedin']),
  kind: z.enum(['reply', 'auto_reply', 'unsubscribe']),
  fromAddress: z.string(),
  fromName: z.string(),
  subject: z.string(),
  body: z.string(),
  campaignId: z.string().nullable(),
  campaignName: z.string().nullable(),
  targetId: z.string().nullable(),
  targetFirstName: z.string().nullable(),
  targetLastName: z.string().nullable(),
  targetCompanyName: z.string().nullable(),
  label: z.enum(LABELS).nullable(),
  labelSource: z.enum(['llm', 'manual']).nullable(),
  receivedAt: z.string(),
  readAt: z.string().nullable(),
  senderAuthenticated: z.boolean().nullable(),
  targetRole: z.string().nullable(),
  // The person's sequence: `paused` until `targetPausedUntil` after an out-of-office (#346).
  targetStatus: z.enum(['active', 'draft', 'paused', 'excluded', 'replied']).nullable(),
  targetPausedUntil: z.string().nullable(),
}) satisfies z.ZodType<Camel<Schema<'InboxMessage'>>>;

const replySchema = z.looseObject({
  id: z.string(),
  channel: z.enum(['email', 'linkedin']),
  subject: z.string(),
  body: z.string(),
  status: z.enum(['sending', 'sent', 'failed', 'unknown']),
  errorCode: z.string().nullable(),
  createdAt: z.string(),
  deliveredAt: z.string().nullable(),
}) satisfies z.ZodType<Camel<Schema<'InboxAnswer'>>>;

const inboxSchema = z.looseObject({
  messages: z.array(messageSchema),
  counts: z.object({
    all: z.number(),
    unread: z.number(),
    unsubscribe: z.number(),
    none: z.number(),
    interested: z.number(),
    meetingRequest: z.number(),
    notInterested: z.number(),
    notNow: z.number(),
    other: z.number(),
  }),
}) satisfies z.ZodType<Camel<Schema<'Inbox'>>>;

/** Why the thread has no answer box; a send refused for one of these answers the same code. */
export const REPLY_BLOCKED = [
  'INBOX.UNSUBSCRIBED',
  'INBOX.EXCLUDED',
  'INBOX.NO_RECIPIENT',
  'INBOX.NO_MAILBOX',
  'INBOX.NO_LINKEDIN',
  'INBOX.ACCOUNT_PAUSED',
] as const;

const threadSchema = z.looseObject({
  message: messageSchema,
  sent: z.array(
    z.looseObject({
      step: z.number(),
      channel: z.enum(['email', 'linkedin_connect', 'linkedin_message']),
      subject: z.string(),
      body: z.string().nullable(),
      deliveredAt: z.string(),
    }),
  ),
  replies: z.array(replySchema),
  draft: z.string().nullable(),
  replyFrom: z.string().nullable(),
  replyBlocked: z.enum(REPLY_BLOCKED).nullable(),
  replyHold: replyHoldSchema.nullable(),
  // A «Non ora» reply's reminder: the day it comes back to «Da leggere», and when it did.
  reminder: z.looseObject({ remindOn: z.string(), surfacedAt: z.string().nullable() }).nullable(),
}) satisfies z.ZodType<Camel<Schema<'Thread'>>>;

export const PAGE_SIZE = 50;

export async function getInbox(filters: {
  label?: string;
  channel?: string;
  kind?: string;
  unread?: boolean;
  campaign_id?: string;
  sort?: string;
  offset: number;
}) {
  return httpClient.get('/v1/inbox', {
    token: await getToken(),
    params: { ...filters, limit: PAGE_SIZE },
    schema: inboxSchema,
  });
}

/** One reply as a thread (#187). Opening it marks it read unless `markRead` is false. */
export async function getThread(id: string, markRead: boolean) {
  return httpClient.get(`/v1/inbox/${encodeURIComponent(id)}`, {
    token: await getToken(),
    params: { mark_read: markRead },
    schema: threadSchema,
  });
}

// A mailbox send can take the provider's full 30 s.
export async function sendReply(id: string, body: string) {
  return httpClient.post(
    `/v1/inbox/${encodeURIComponent(id)}/reply`,
    { body },
    { token: await getToken(), timeoutMs: 45_000, schema: replySchema },
  );
}

export async function relabel(id: string, label: Label) {
  return httpClient.patch(
    `/v1/inbox/${encodeURIComponent(id)}`,
    { label },
    { token: await getToken() },
  );
}
