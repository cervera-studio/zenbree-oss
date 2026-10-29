'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { z } from 'zod';
import {
  createSegment,
  deleteSegment,
  editSegment,
  getListColumns,
  getWhy,
  previewSegment,
  publishSegment,
  refreshSegment,
} from '@/lib/segments/api';
import { complete } from '@/lib/segments/rule';
import { ruleSchemas } from '@/lib/segments/rule-schema';
import { AppError, fail } from '@/lib/types';

const id = z.uuid();
const entity = z.enum(['person', 'company']);
const { group } = ruleSchemas(z.strictObject);
const rule = group.refine(complete);
const name = z.string().trim().min(1).max(100);

function invalid() {
  return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
}

function refresh(segmentId: string) {
  revalidatePath('/segments');
  revalidatePath(`/segments/${segmentId}`);
  revalidatePath(`/segments/${segmentId}/members`);
}

export async function previewAction(input: unknown) {
  const parsed = z.object({ entity, rule, segment_id: id.optional() }).safeParse(input);
  if (!parsed.success) return invalid();
  return previewSegment(parsed.data);
}

export async function createSegmentAction(input: unknown) {
  const parsed = z.object({ name, entity, rule }).safeParse(input);
  if (!parsed.success) return invalid();
  const result = await createSegment(parsed.data);
  if (result.success) revalidatePath('/segments');
  return result;
}

export async function editSegmentAction(segmentId: string, input: unknown) {
  const parsed = z
    .object({
      version: z.int().min(1),
      name: name.optional(),
      rule: rule.optional(),
      include_new: z.boolean().optional(),
    })
    .safeParse(input);
  if (!id.safeParse(segmentId).success || !parsed.success) return invalid();
  const result = await editSegment(segmentId, parsed.data);
  if (result.success) refresh(segmentId);
  return result;
}

export async function publishSegmentAction(segmentId: string, includeCurrent: unknown) {
  if (!id.safeParse(segmentId).success || typeof includeCurrent !== 'boolean') return invalid();
  const result = await publishSegment(segmentId, includeCurrent);
  if (result.success) refresh(segmentId);
  return result;
}

export async function refreshSegmentAction(segmentId: string) {
  if (!id.safeParse(segmentId).success) return invalid();
  const result = await refreshSegment(segmentId);
  if (result.success) refresh(segmentId);
  return result;
}

export async function deleteSegmentAction(segmentId: string) {
  if (!id.safeParse(segmentId).success) return invalid();
  const result = await deleteSegment(segmentId);
  if (!result.success) return result;
  revalidatePath('/segments');
  redirect('/segments');
}

export async function whyAction(segmentId: string, identityId: string) {
  if (!id.safeParse(segmentId).success || !id.safeParse(identityId).success) return invalid();
  return getWhy(segmentId, identityId);
}

export async function listColumnsAction(listId: string) {
  if (!id.safeParse(listId).success) return invalid();
  return getListColumns(listId);
}
