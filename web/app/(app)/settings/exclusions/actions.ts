'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { getSession } from '@/lib/auth-token';
import { addExclusions, deleteExclusion } from '@/lib/exclusions/api';
import { EXCLUSION_KINDS, MAX_EXCLUSIONS_PER_IMPORT } from '@/lib/exclusions/csv';
import { AppError, fail } from '@/lib/types';

const PATH = '/settings/exclusions';
const itemsSchema = z
  .array(
    z.object({
      kind: z.enum(EXCLUSION_KINDS).optional(),
      value: z.string().trim().min(1).max(1000),
    }),
  )
  .min(1)
  .max(MAX_EXCLUSIONS_PER_IMPORT);
const idSchema = z.string().uuid();

export async function addExclusionsAction(items: unknown) {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const parsed = itemsSchema.safeParse(items);
  if (!parsed.success) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid exclusions'));
  }
  const result = await addExclusions(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}

export async function deleteExclusionAction(id: string) {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid exclusion id'));
  }
  const result = await deleteExclusion(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}
