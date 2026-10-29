'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { getSession } from '@/lib/auth-token';
import { AppError, fail } from '@/lib/types';
import { createView, deleteView, renameView } from '@/lib/views/api';

const idSchema = z.string().uuid();
const nameSchema = z.string().trim().min(1).max(100);
const createSchema = z.object({
  listId: idSchema,
  name: nameSchema,
  tab: z.enum(['all', 'delivered', 'pending', 'no_contact', 'excluded']),
  query: z.string().max(200),
  minFit: z.number().int().min(0).max(100).nullable(),
});

const signedOut = () => fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
const invalid = () => fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid saved view'));

export async function createViewAction(input: unknown) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = createSchema.safeParse(input);
  if (!parsed.success) return invalid();
  const result = await createView(parsed.data);
  if (result.success) revalidatePath('/views');
  return result;
}

export async function renameViewAction(id: string, name: string) {
  if (!(await getSession())?.user) return signedOut();
  const parsedId = idSchema.safeParse(id);
  const parsedName = nameSchema.safeParse(name);
  if (!parsedId.success || !parsedName.success) return invalid();
  const result = await renameView(parsedId.data, parsedName.data);
  if (result.success) revalidatePath('/views');
  return result;
}

export async function deleteViewAction(id: string) {
  if (!(await getSession())?.user) return signedOut();
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) return invalid();
  const result = await deleteView(parsed.data);
  if (result.success) revalidatePath('/views');
  return result;
}
