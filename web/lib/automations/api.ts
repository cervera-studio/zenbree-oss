import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { AppError, fail, type Result, succeed } from '@/lib/types';
import { TRIGGER_KINDS } from './triggers';

const STEP_KINDS = [
  'find_people',
  'qualify',
  'find_emails',
  'add_to_campaign',
  'add_to_instantly',
  'write_hubspot',
] as const;
export type StepKind = (typeof STEP_KINDS)[number];

const counts = z.record(z.string(), z.number());
// `toCamelCase` is deep: the keys of the stored JSON arrive in camelCase, the values do not change.
const summarySchema = z.looseObject({
  taken: z
    .looseObject({
      people: z.number(),
      refusedBeforeAnyCredit: counts.optional(),
      // Not an enum: the page says a reason it does not know in general words.
      nobody: z.string().optional(),
    })
    .optional(),
  signals: z
    .union([
      z.object({ givenUp: z.literal(true), failedRuns: z.number() }),
      z.object({ givenBack: z.literal(true) }),
    ])
    .optional(),
  promoted: z.number().optional(),
  hubspotAfterApproval: z.looseObject({ errorCode: z.string().nullable() }).optional(),
});
const runFields = {
  id: z.string(),
  automationId: z.string(),
  version: z.number(),
  triggerKind: z.enum(TRIGGER_KINDS),
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown']),
  cancelRequested: z.boolean(),
  creditCap: z.number(),
  creditsSpent: z.number(),
  review: z.boolean().nullable(),
  approvedAt: z.string().nullable(),
  summary: summarySchema,
  errorCode: z.string().nullable(),
  createdAt: z.string(),
  startedAt: z.string().nullable(),
  finishedAt: z.string().nullable(),
};
const runSchema = z.looseObject(runFields) satisfies z.ZodType<Camel<Schema<'AutomationRun'>>>;
const stepSchema = z.looseObject({
  position: z.number(),
  kind: z.enum(STEP_KINDS),
  status: z.enum(['pending', 'running', 'succeeded', 'skipped', 'failed', 'uncertain']),
  decision: z.string().nullable(),
  input: z.record(z.string(), z.unknown()),
  output: z.record(z.string(), z.unknown()),
  credits: z.number(),
  attempts: z.number(),
  errorCode: z.string().nullable(),
  startedAt: z.string().nullable(),
  finishedAt: z.string().nullable(),
}) satisfies z.ZodType<Camel<Schema<'RunStep'>>>;
const deliverySchema = z.looseObject({
  id: z.string(),
  webhookId: z.string(),
  receivedAt: z.string(),
  matchedBy: z.enum(['linkedin_url', 'email']),
  rowId: z.string().nullable(),
});
export const instantlyCampaignFields = {
  id: z.string(),
  name: z.string(),
  status: z.number(),
  maySend: z.boolean(),
};
const runInstantlySchema = z.looseObject({
  people: z.number(),
  batchId: z.string().nullable(),
  waitingFor: z
    .enum([
      'approval',
      'paused',
      'creator_gone',
      'connection',
      'other_workspace',
      'other_batch',
      'starting',
    ])
    .nullable(),
  campaign: z.looseObject(instantlyCampaignFields),
});
const runSignalSchema = z.looseObject({
  accountId: z.string(),
  accountName: z.string().nullable(),
  signals: z.array(
    z.looseObject({
      type: z.string(),
      title: z.string().nullable(),
      score: z.number(),
      occurredAt: z.string(),
    }),
  ),
});
const runDetailSchema = z.looseObject({
  ...runFields,
  steps: z.array(stepSchema),
  delivery: deliverySchema.nullable(),
  instantly: runInstantlySchema.nullable(),
  signal: runSignalSchema.nullable(),
}) satisfies z.ZodType<Camel<Schema<'AutomationRunDetail'>>>;
const webhookSchema = z.looseObject({
  url: z.string().nullable(),
  rotatedAt: z.string(),
  pending: z.number(),
  waitingForApproval: z.boolean(),
}) satisfies z.ZodType<Camel<Schema<'AutomationWebhook'>>>;
const secretSchema = z.looseObject({
  url: z.string().nullable(),
  secret: z.string(),
  rotatedAt: z.string(),
}) satisfies z.ZodType<Camel<Schema<'WebhookSecret'>>>;
const signalSchema = z.looseObject({
  watchlistId: z.string().nullable(),
  watchlistName: z.string().nullable(),
  minScore: z.number(),
  since: z.string(),
}) satisfies z.ZodType<Camel<Schema<'AutomationSignal'>>>;
export const automationSchema = z.looseObject({
  id: z.string(),
  projectId: z.string(),
  userId: z.string().nullable(),
  name: z.string(),
  triggerKind: z.enum(TRIGGER_KINDS),
  cadence: z.enum(['daily', 'weekly']).nullable(),
  nextRunAt: z.string().nullable(),
  enabled: z.boolean(),
  version: z.number(),
  approvedVersion: z.number().nullable(),
  creditCap: z.number(),
  definition: z.looseObject({
    listId: z.string(),
    steps: z.array(z.looseObject({ type: z.enum(STEP_KINDS), campaignId: z.string().optional() })),
  }),
  createdAt: z.string(),
  updatedAt: z.string(),
  lastRun: runSchema.nullable(),
  // Null until the first secret of a webhook automation, and for every other trigger.
  webhook: webhookSchema.nullable(),
  signal: signalSchema.nullable(),
});
const automationsSchema = z.looseObject({ automations: z.array(automationSchema) });
const runsSchema = z.looseObject({ runs: z.array(runSchema) }) satisfies z.ZodType<
  Camel<Schema<'AutomationRuns'>>
>;

export type Automation = z.infer<typeof automationSchema>;
export type AutomationRun = z.infer<typeof runSchema>;
export type AutomationRunDetail = z.infer<typeof runDetailSchema>;
export type RunStep = z.infer<typeof stepSchema>;
export type AutomationSignal = z.infer<typeof signalSchema>;
export type AutomationWebhook = z.infer<typeof webhookSchema>;
export type WebhookSecret = z.infer<typeof secretSchema>;
export type RunDelivery = z.infer<typeof deliverySchema>;

const BASE = '/v1/automations';
const at = (id: string, path = '') => `${BASE}/${encodeURIComponent(id)}${path}`;
const runAt = (id: string, runId: string, path = '') =>
  at(id, `/runs/${encodeURIComponent(runId)}${path}`);

export async function getAutomations() {
  return httpClient.get(BASE, { token: await getToken(), schema: automationsSchema });
}

export async function setEnabled(id: string, enabled: boolean) {
  return httpClient.patch(
    at(id),
    { enabled },
    { token: await getToken(), schema: automationSchema },
  );
}

/** The automation's live run when one is queued or running, else a new queued run. */
export async function runAutomation(id: string) {
  return httpClient.post(at(id, '/run'), undefined, { token: await getToken(), schema: runSchema });
}

/** A new signing secret, in this answer only: the old one stops working at once, the URL stays. */
export async function makeWebhookSecret(id: string) {
  return httpClient.post(at(id, '/webhook/secret'), undefined, {
    token: await getToken(),
    schema: secretSchema,
  });
}

export async function getRuns(id: string) {
  return httpClient.get(at(id, '/runs'), { token: await getToken(), schema: runsSchema });
}

export async function getRun(id: string, runId: string) {
  return httpClient.get(runAt(id, runId), { token: await getToken(), schema: runDetailSchema });
}

export async function cancelRun(id: string, runId: string) {
  return httpClient.post(runAt(id, runId, '/cancel'), undefined, {
    token: await getToken(),
    schema: runDetailSchema,
  });
}

export async function approveRun(id: string, runId: string) {
  return httpClient.post(runAt(id, runId, '/approve'), undefined, {
    token: await getToken(),
    schema: runDetailSchema,
  });
}

/** The API has no route for one automation: it is the list's entry, which also carries its last run. */
export async function getAutomation(id: string): Promise<Result<Automation>> {
  const result = await getAutomations();
  if (!result.success) return result;
  const found = result.data.automations.find((a) => a.id === id);
  return found
    ? succeed(found)
    : fail(new AppError('AUTOMATION.NOT_FOUND', 'automation not found', 404));
}
