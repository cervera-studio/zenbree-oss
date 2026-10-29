/** The back office's chat calls: a user's chat sessions and one transcript (`/v1/admin/chat-sessions`). */
import 'server-only';

import { z } from 'zod';
import { auth, page } from '@/lib/admin/api';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { toolSummarySchema } from '@/lib/chat/tool-summary';
import { httpClient } from '@/lib/http-client';

const chatSession = z.looseObject({
  id: z.string(),
  // null once its creator's account is deleted (#333): the chat stays the organization's
  userId: z.string().nullable(),
  email: z.string().nullable(),
  title: z.string().nullable(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

const chatSessionsSchema = page(
  chatSession.extend({
    messageCount: z.number(),
    companies: z.number(),
    people: z.number(),
    creditsSpent: z.number(),
    // null: no turn finished yet
    outcome: z.enum(['results', 'stuck', 'no_results']).nullable(),
  }),
);

const toolCall = z.looseObject({
  id: z.string().nullish(),
  name: z.string(),
  input: z.record(z.string(), z.unknown()).default({}),
  result: z.string().nullish(),
  creditsUsed: z.number().default(0),
  verdict: z.enum(['success', 'failed']).nullish(),
  // The vendors it called, in order; absent before #352 and for calls that called none.
  vendors: z.array(z.string()).nullish(),
  summary: toolSummarySchema.nullish(),
  brief: z.record(z.string(), z.unknown()).nullish(),
  memorySaved: z.string().nullish(),
  campaign: z.record(z.string(), z.unknown()).nullish(),
}) satisfies z.ZodType<Camel<Schema<'AdminToolCallRecord'>>>;

// ask_user / funnel / plan pause (contracts: chat_messages.question). Options were
// plain strings before #211.
const question = z.looseObject({
  question: z.string().optional(),
  options: z.array(z.union([z.string(), z.looseObject({ label: z.string() })])).optional(),
  plan: z.looseObject({ estimatedCredits: z.number().optional() }).nullish(),
});

const chatSessionDetailSchema = chatSession.extend({
  name: z.string().nullable(),
  messages: z.array(
    z.looseObject({
      id: z.string(),
      role: z.string(),
      content: z.string().nullable(),
      toolCalls: z.array(toolCall).nullable(),
      question: question.nullable(),
      creditsUsed: z.number(),
      createdAt: z.string(),
    }),
  ),
  summary: z.looseObject({
    jobId: z.string().nullable(),
    // Net of creditsReturned, the credits given back for undelivered rows.
    creditsSpent: z.number(),
    creditsReturned: z.number(),
    llmCostUsd: z.number(),
    vendorCostUsd: z.number(),
    companies: z.number(),
    people: z.number(),
    eurPerUsd: z.number(),
    eurPer1000Credits: z.number().nullable(),
    revenueEur: z.number().nullable(),
    marginEur: z.number().nullable(),
  }),
});
export type ChatSessionDetail = z.infer<typeof chatSessionDetailSchema>;

export async function getChatSessions(userId: string) {
  return httpClient.get('/v1/admin/chat-sessions', {
    ...(await auth()),
    params: { user_id: userId, limit: 200 },
    schema: chatSessionsSchema,
  });
}

export async function getChatSession(id: string) {
  return httpClient.get(`/v1/admin/chat-sessions/${encodeURIComponent(id)}`, {
    ...(await auth()),
    schema: chatSessionDetailSchema,
  });
}
