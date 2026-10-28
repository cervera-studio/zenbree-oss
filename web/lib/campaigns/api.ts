import 'server-only';

import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { getProjects } from '@/lib/projects/server';
import { type CampaignField, campaignFieldSchema } from './fields';
import { senderHoldSchema } from './mailbox-state';
import { CHANNELS, type Step, stepSchema, VARIANTS, type Variant } from './steps';

const count = z.union([z.number(), z.string().transform(Number)]);
const summary = {
  id: z.string(),
  projectId: z.string(),
  ownerId: z.string().nullable(),
  name: z.string(),
  approveBeforeSending: z.boolean(),
  blockActiveDuplicates: z.boolean(),
  status: z.enum(['draft', 'active', 'paused', 'stopped', 'completed']),
  createdAt: z.string(),
  activatedAt: z.string().nullable(),
  targetCount: count,
  sent: count,
  replied: count,
  failed: count,
  awaitingApproval: count,
  awaitingApprovalHeld: count,
  listId: z.string().nullable(),
  chatSessionId: z.string().nullable(),
  listName: z.string().nullable(),
  stepChannels: z.array(z.enum(CHANNELS)),
  plannedSends: count,
  stepsDone: count,
  contacted: count,
  repliedContacted: count,
};
const summarySchema = z.looseObject(summary) satisfies z.ZodType<CampaignSummary>;
const campaignSchema = z.looseObject({
  ...summary,
  steps: z.array(stepSchema),
  fields: z.array(campaignFieldSchema),
  linkedinRiskNotice: z.string(),
  sendWindowStartHour: z.number(),
  sendWindowEndHour: z.number(),
  sendWindowDays: z.array(z.number()),
  bPercent: z.number(),
  allocationPausedAt: z.string().nullable(),
  sentToday: count,
  dailyCap: count,
  interested: count,
  autoReplies: count,
  invalidEmail: count,
  unreadReplies: count,
}) satisfies z.ZodType<Campaign>;
// `steps` is the shared editor/request `Step`, which has no response-only `personalised`.
export type Campaign = Omit<Camel<Schema<'Campaign'>>, 'steps' | 'fields'> & {
  steps: Step[];
  fields: CampaignField[];
};
const campaignsSchema = z.looseObject({
  campaigns: z.array(summarySchema),
}) satisfies z.ZodType<Camel<Schema<'Campaigns'>>>;
export type CampaignSummary = Camel<Schema<'CampaignSummary'>>;

const variantSchema = z.enum(VARIANTS).nullable();
const renderedSchema = z.looseObject({
  step: z.number(),
  channel: z.enum(CHANNELS),
  subject: z.string(),
  body: z.string(),
  error: z.string().nullable(),
  signOff: z.string(),
  footer: z.string(),
});
export const previewSchema = z.looseObject({
  items: z.array(
    z.looseObject({
      target: z.looseObject({
        firstName: z.string(),
        lastName: z.string(),
        companyName: z.string(),
        email: z.string().nullable(),
        linkedinUrl: z.string(),
        role: z.string(),
      }),
      steps: z.array(renderedSchema),
      stepsB: z.array(renderedSchema).nullable(),
      variant: variantSchema,
      missing: z.array(z.string()),
    }),
  ),
}) satisfies z.ZodType<Preview>;
export type Preview = Camel<Schema<'Preview'>>;

const holdSchema = z
  .enum(['stop_failed', 'stop_pending', 'stopped', 'held_by_instantly'])
  .nullable();

const pendingSchema = z.looseObject({
  id: z.string(),
  step: z.number(),
  channel: z.enum(['email', 'linkedin_connect', 'linkedin_message']),
  recipient: z.string(),
  subject: z.string(),
  body: z.string().nullable(),
  createdAt: z.string(),
  firstName: z.string(),
  lastName: z.string(),
  companyName: z.string(),
  variant: variantSchema,
  // The earliest moment the dispatcher could send it once approved.
  sendAt: z.string().nullable(),
  sender: z.looseObject({ address: z.string().nullable(), name: z.string().nullable() }).nullable(),
  hold: holdSchema,
  senderHold: senderHoldSchema.nullable(),
}) satisfies z.ZodType<PendingSend>;
export type PendingSend = Camel<Schema<'QueuedSend'>>;
const approvalsSchema = z.looseObject({ items: z.array(pendingSchema) }) satisfies z.ZodType<
  Camel<Schema<'QueuedSends'>>
>;

const targetSchema = z.looseObject({
  id: z.string(),
  firstName: z.string(),
  lastName: z.string(),
  companyName: z.string(),
  status: z.enum(['active', 'draft', 'paused', 'excluded', 'replied']),
  nextStep: z.number(),
  // Set while `paused`: when an out-of-office pause ends (#346).
  pausedUntil: z.string().nullable(),
  awaitingApproval: z.boolean(),
  invalidEmail: z.boolean(),
  lastSentStep: z.number().nullable(),
  lastSentAt: z.string().nullable(),
  messageId: z.string().nullable(),
  messageKind: z.enum(['reply', 'auto_reply', 'unsubscribe']).nullable(),
  messageLabel: z
    .enum(['interested', 'meeting_request', 'not_interested', 'not_now', 'other'])
    .nullable(),
  messageAt: z.string().nullable(),
  signalTitle: z.string().nullable(),
  hold: holdSchema,
  emailSender: z.enum(['native', 'instantly']).nullable(),
  sender: z.string().nullable(),
  senderHold: senderHoldSchema.nullable(),
  frozenAt: z.string().nullable(),
  missing: z.array(z.string()),
  variant: variantSchema,
}) satisfies z.ZodType<Target>;
export type Target = Camel<Schema<'Target'>>;
const targetsSchema = z.looseObject({ items: z.array(targetSchema) });

const activitySchema = z.looseObject({
  items: z.array(
    z.looseObject({
      id: z.string(),
      kind: z.enum(['sent', 'approved', 'reply', 'auto_reply', 'unsubscribe']),
      at: z.string(),
      targetId: z.string().nullable(),
      firstName: z.string().nullable(),
      lastName: z.string().nullable(),
      companyName: z.string().nullable(),
      step: z.number().nullable(),
      channel: z.enum([
        'email',
        'linkedin',
        'linkedin_connect',
        'linkedin_message',
        'hubspot_task',
      ]),
      label: z
        .enum(['interested', 'meeting_request', 'not_interested', 'not_now', 'other'])
        .nullable(),
    }),
  ),
  nextCursor: z.string().nullable(),
}) satisfies z.ZodType<Activity>;
export type Activity = Camel<Schema<'Activity'>>;
export type ActivityEvent = Activity['items'][number];
const ACTIVITY_PAGE = 20;

const listsSchema = z.looseObject({
  lists: z.array(z.looseObject({ id: z.string(), name: z.string(), entity: z.string() })),
});

const BASE = '/v1/campaigns';
const at = (id: string, path = '') => `${BASE}/${encodeURIComponent(id)}${path}`;

export async function getCampaigns() {
  return httpClient.get(BASE, { token: await getToken(), schema: campaignsSchema });
}

export async function getCampaign(id: string) {
  return httpClient.get(at(id), { token: await getToken(), schema: campaignSchema });
}

export async function getTargets(id: string) {
  return httpClient.get(at(id, '/targets'), { token: await getToken(), schema: targetsSchema });
}

export async function getActivity(id: string, after?: string) {
  const query = new URLSearchParams({ limit: String(ACTIVITY_PAGE) });
  if (after) query.set('after', after);
  return httpClient.get(at(id, `/activity?${query}`), {
    token: await getToken(),
    schema: activitySchema,
  });
}

export async function getPeopleLists() {
  const projects = await getProjects();
  const project = projects.success ? projects.data.active?.id : undefined;
  if (!project) return [];
  const result = await httpClient.get(`/v1/lists?project_id=${encodeURIComponent(project)}`, {
    token: await getToken(),
    schema: listsSchema,
  });
  return result.success ? result.data.lists.filter((l) => l.entity === 'person') : [];
}

const listClientSchema = z.looseObject({ entity: z.string(), projectId: z.string() });

export async function otherClientOfPeopleList(listId: string): Promise<string | null> {
  const [projects, list] = await Promise.all([
    getProjects(),
    httpClient.get(`/v1/lists/${encodeURIComponent(listId)}`, {
      token: await getToken(),
      schema: listClientSchema,
    }),
  ]);
  if (!projects.success || !list.success || list.data.entity !== 'person') return null;
  const { projectId } = list.data;
  const { active, projects: all } = projects.data;
  return projectId !== active?.id && all.some((p) => p.id === projectId) ? projectId : null;
}

export async function createCampaign(name: string, listId: string) {
  return httpClient.post(
    BASE,
    { name, listId },
    { token: await getToken(), schema: campaignSchema },
  );
}

export async function updateCampaign(
  id: string,
  patch: {
    name?: string;
    steps?: Step[];
    fields?: CampaignField[];
    blockActiveDuplicates?: boolean;
  },
) {
  return httpClient.patch(at(id), patch, { token: await getToken(), schema: campaignSchema });
}

export async function deleteCampaign(id: string) {
  return httpClient.delete(at(id), { token: await getToken() });
}

export async function transitionCampaign(id: string, verb: 'activate' | 'pause' | 'resume') {
  return httpClient.post(at(id, `/${verb}`), undefined, {
    token: await getToken(),
    schema: campaignSchema,
  });
}

/** B's share of the people who join, in a draft; or a pause of the A/B allocation once live (#934). */
export async function setAllocation(id: string, body: { bPercent: number } | { paused: boolean }) {
  return httpClient.put(at(id, '/allocation'), body, {
    token: await getToken(),
    schema: campaignSchema,
  });
}

const regeneratedSchema = z.looseObject({
  active: z.number(),
  renewed: z.number(),
  waiting: z.number(),
  kept: z.number(),
}) satisfies z.ZodType<Regenerated>;
type Regenerated = Camel<Schema<'Regenerated'>>;

export async function regenerateValues(id: string) {
  return httpClient.post(at(id, '/targets/regenerate'), undefined, {
    token: await getToken(),
    schema: regeneratedSchema,
  });
}

export async function sendTestEmail(id: string, step: number, variant: Variant) {
  return httpClient.post(
    at(id, '/test-email'),
    { step, variant },
    { token: await getToken(), schema: z.looseObject({ to: z.string() }) },
  );
}

export async function getApprovals(id: string) {
  return httpClient.get(at(id, '/approvals'), { token: await getToken(), schema: approvalsSchema });
}

export async function decideSends(id: string, verb: 'approve' | 'reject', sendIds: string[]) {
  // The API counts what it changed: a message decided elsewhere meanwhile is not in it.
  return httpClient.post(
    at(id, `/approvals/${verb}`),
    { sendIds },
    {
      token: await getToken(),
      schema: z.looseObject({
        approved: z.number().optional(),
        held: z.number().optional(),
        rejected: z.number().optional(),
      }),
    },
  );
}

// «Annulla» after a reject (#230): `restored` can be fewer than asked once the worker moved a person on.
export async function undoReject(id: string, sendIds: string[]) {
  return httpClient.post(
    at(id, '/approvals/undo-reject'),
    { sendIds },
    { token: await getToken(), schema: z.looseObject({ restored: z.number() }) },
  );
}

export async function editSend(id: string, sendId: string, subject: string, body: string) {
  return httpClient.patch(
    at(id, `/approvals/${encodeURIComponent(sendId)}`),
    { subject, body },
    { token: await getToken() },
  );
}
