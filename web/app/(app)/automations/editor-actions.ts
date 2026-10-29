'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { z } from 'zod';
import { definitionSchema, MAX_CREDIT_CAP } from '@/lib/automations/definition';
import {
  createAutomation,
  deleteAutomation,
  previewAutomation,
  updateAutomation,
} from '@/lib/automations/editor-api';
import { signalScoreOk } from '@/lib/automations/signal-draft';
import { AppError, fail } from '@/lib/types';

const id = z.uuid();
const planSchema = z.object({
  definition: definitionSchema,
  creditCap: z.int().min(1).max(MAX_CREDIT_CAP),
});
const triggerSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('manual') }),
  z.object({ kind: z.literal('schedule'), cadence: z.enum(['daily', 'weekly']) }),
  z.object({ kind: z.literal('webhook') }),
  // A null watchlist reaches the API, whose problem names it.
  z.object({
    kind: z.literal('signal'),
    watchlistId: z.uuid().nullable(),
    minScore: z.number().refine(signalScoreOk),
  }),
]);
const bodySchema = planSchema.extend({
  name: z.string().trim().min(1).max(120),
  trigger: triggerSchema,
});

function invalid() {
  return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
}

function refresh(automationId: string) {
  revalidatePath('/automations');
  revalidatePath(`/automations/${automationId}`);
  revalidatePath(`/automations/${automationId}/edit`);
}

export async function previewAction(plan: unknown, trigger: unknown, automationId?: string) {
  const parsed = planSchema.safeParse(plan);
  const parsedTrigger = triggerSchema.safeParse(trigger);
  if (
    !parsed.success ||
    !parsedTrigger.success ||
    (automationId !== undefined && !id.safeParse(automationId).success)
  ) {
    return invalid();
  }
  return previewAutomation(parsed.data, parsedTrigger.data, automationId);
}

export async function createAutomationAction(body: unknown) {
  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) return invalid();
  const result = await createAutomation(parsed.data);
  if (!result.success) return result;
  revalidatePath('/automations');
  redirect(`/automations/${result.data.id}`);
}

export async function updateAutomationAction(automationId: string, body: unknown) {
  const parsed = bodySchema.safeParse(body);
  if (!id.safeParse(automationId).success || !parsed.success) return invalid();
  const result = await updateAutomation(automationId, parsed.data);
  if (result.success) refresh(automationId);
  return result;
}

export async function deleteAutomationAction(automationId: string) {
  if (!id.safeParse(automationId).success) return invalid();
  const result = await deleteAutomation(automationId);
  if (!result.success) return result;
  revalidatePath('/automations');
  redirect('/automations');
}
