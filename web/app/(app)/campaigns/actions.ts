'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { z } from 'zod';
import {
  createCampaign,
  decideSends,
  deleteCampaign,
  editSend,
  getActivity,
  regenerateValues,
  sendTestEmail,
  setAllocation,
  transitionCampaign,
  undoReject,
  updateCampaign,
} from '@/lib/campaigns/api';
import { livePreview } from '@/lib/campaigns/builder-api';
import { MAX_FALLBACK, MAX_FIELDS, SIGNAL_KEYS } from '@/lib/campaigns/fields';
import { setCampaignSenders } from '@/lib/campaigns/senders';
import { CHANNELS, MAX_STEPS, VARIANTS, type Variant } from '@/lib/campaigns/steps';
import { AppError, fail, type Result } from '@/lib/types';

const id = z.uuid();
const ids = z.array(z.uuid()).min(1).max(1000);
const fieldsSchema = z
  .array(
    z.object({
      token: z.string().max(40),
      source: z.union([
        z.object({ kind: z.literal('column'), key: z.string().min(1).max(200) }),
        z.object({ kind: z.literal('signal'), key: z.enum(SIGNAL_KEYS) }),
      ]),
      fallback: z.string().trim().min(1).max(MAX_FALLBACK).nullable(),
    }),
  )
  .max(MAX_FIELDS);
const createSchema = z.object({ name: z.string().trim().min(1).max(200), listId: id });
// Shape only; the API's validate_steps owns the sequencing rules.
const patchSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  steps: z
    .array(
      z.object({
        step: z.number().int(),
        channel: z.enum(CHANNELS),
        // Generous bounds only: the API refuses copy over its limits with CAMPAIGN.COPY_TOO_LONG.
        subject: z.string().max(10_000),
        body: z.string().max(50_000),
        sendAfterDays: z.number().int().min(0),
        threaded: z.boolean(),
        // Without it zod strips copy B, and a builder save ends the A/B test (#934).
        b: z.object({ subject: z.string().max(10_000), body: z.string().max(50_000) }).nullish(),
      }),
    )
    .max(MAX_STEPS)
    .optional(),
  fields: fieldsSchema.optional(),
  blockActiveDuplicates: z.boolean().optional(),
});
const editSchema = z.object({
  subject: z.string().trim().max(10_000),
  body: z.string().trim().max(50_000),
});

function invalid() {
  return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
}

function refresh<T>(campaignId: string, result: Result<T>): Result<T> {
  if (result.success) {
    revalidatePath(`/campaigns/${campaignId}`);
    // A literal path revalidates that page only, not the approval screen under it.
    revalidatePath(`/campaigns/${campaignId}/approvals`);
  }
  return result;
}

export async function createCampaignAction(name: string, listId: string) {
  const parsed = createSchema.safeParse({ name, listId });
  if (!parsed.success) return invalid();
  const result = await createCampaign(parsed.data.name, parsed.data.listId);
  if (!result.success) return result;
  revalidatePath('/campaigns');
  redirect(`/campaigns/${result.data.id}`);
}

export async function updateCampaignAction(campaignId: string, patch: unknown) {
  const parsed = patchSchema.safeParse(patch);
  if (!id.safeParse(campaignId).success || !parsed.success) return invalid();
  return refresh(campaignId, await updateCampaign(campaignId, parsed.data));
}

// The API owns the client check and the cap; this only bounds the request.
const senderIds = z.array(id).max(100);

export async function setCampaignSendersAction(campaignId: string, picked: unknown) {
  const parsed = senderIds.safeParse(picked);
  if (!id.safeParse(campaignId).success || !parsed.success) return invalid();
  return refresh(campaignId, await setCampaignSenders(campaignId, parsed.data));
}

export async function deleteCampaignAction(campaignId: string) {
  if (!id.safeParse(campaignId).success) return invalid();
  const result = await deleteCampaign(campaignId);
  if (!result.success) return result;
  revalidatePath('/campaigns');
  redirect('/campaigns');
}

export async function transitionCampaignAction(
  campaignId: string,
  verb: 'activate' | 'pause' | 'resume',
) {
  if (!id.safeParse(campaignId).success || !['activate', 'pause', 'resume'].includes(verb)) {
    return invalid();
  }
  return refresh(campaignId, await transitionCampaign(campaignId, verb));
}

const allocationSchema = z.union([
  z.strictObject({ bPercent: z.number().int().min(10).max(90).multipleOf(10) }),
  z.strictObject({ paused: z.boolean() }),
]);

export async function allocationAction(campaignId: string, body: unknown) {
  const parsed = allocationSchema.safeParse(body);
  if (!id.safeParse(campaignId).success || !parsed.success) return invalid();
  return refresh(campaignId, await setAllocation(campaignId, parsed.data));
}

export async function regenerateValuesAction(campaignId: string) {
  if (!id.safeParse(campaignId).success) return invalid();
  return refresh(campaignId, await regenerateValues(campaignId));
}

const liveSchema = z.object({
  steps: z
    .array(
      z.object({
        channel: z.enum(CHANNELS),
        subject: z.string().max(2000),
        body: z.string().max(20_000),
        threaded: z.boolean(),
        b: z.object({ subject: z.string().max(2000), body: z.string().max(20_000) }).nullish(),
      }),
    )
    .max(MAX_STEPS),
  fields: fieldsSchema,
});

// The builder's live preview (#217): renders what is typed, stores nothing.
export async function livePreviewAction(campaignId: string, steps: unknown, fields: unknown) {
  const parsed = liveSchema.safeParse({ steps, fields });
  if (!id.safeParse(campaignId).success || !parsed.success) return invalid();
  return livePreview(campaignId, parsed.data.steps, parsed.data.fields);
}

export async function testEmailAction(campaignId: string, step: number, variant: Variant = 'a') {
  if (!id.safeParse(campaignId).success || !Number.isInteger(step) || !VARIANTS.includes(variant)) {
    return invalid();
  }
  return sendTestEmail(campaignId, step, variant);
}

export async function decideSendsAction(
  campaignId: string,
  verb: 'approve' | 'reject',
  sendIds: string[],
) {
  if (
    !id.safeParse(campaignId).success ||
    !ids.safeParse(sendIds).success ||
    !['approve', 'reject'].includes(verb)
  ) {
    return invalid();
  }
  return refresh(campaignId, await decideSends(campaignId, verb, sendIds));
}

export async function undoRejectAction(campaignId: string, sendIds: string[]) {
  if (!id.safeParse(campaignId).success || !ids.safeParse(sendIds).success) return invalid();
  return refresh(campaignId, await undoReject(campaignId, sendIds));
}

export async function editSendAction(
  campaignId: string,
  sendId: string,
  subject: string,
  body: string,
) {
  const parsed = editSchema.safeParse({ subject, body });
  if (!id.safeParse(campaignId).success || !id.safeParse(sendId).success || !parsed.success) {
    return invalid();
  }
  return refresh(
    campaignId,
    await editSend(campaignId, sendId, parsed.data.subject, parsed.data.body),
  );
}

// «Carica altri» on the activity panel: a read, so nothing to revalidate.
export async function loadActivityAction(campaignId: string, after: string) {
  if (!id.safeParse(campaignId).success || !z.string().min(1).max(500).safeParse(after).success) {
    return invalid();
  }
  return getActivity(campaignId, after);
}
