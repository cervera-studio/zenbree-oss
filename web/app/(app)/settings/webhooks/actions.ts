'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { getSession } from '@/lib/auth-token';
import { AppError, fail } from '@/lib/types';
import { createWebhook, deleteWebhook, getDeliveries, testWebhook } from '@/lib/webhooks/api';

const PATH = '/settings/webhooks';
const idSchema = z.string().uuid();
const createSchema = z.object({
  url: z.string().trim().min(9).max(2000),
  label: z.string().trim().min(1).max(120),
  eventNames: z.array(z.string().min(1).max(50)).min(1).max(10),
});

const signedOut = () => fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
const invalid = () => fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid webhook request'));

export async function createWebhookAction(input: unknown) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = createSchema.safeParse(input);
  if (!parsed.success) return invalid();
  const result = await createWebhook(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}

export async function deleteWebhookAction(id: string) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) return invalid();
  const result = await deleteWebhook(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}

export async function testWebhookAction(id: string) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) return invalid();
  return testWebhook(parsed.data);
}

export async function getDeliveriesAction(id: string) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) return invalid();
  return getDeliveries(parsed.data);
}
