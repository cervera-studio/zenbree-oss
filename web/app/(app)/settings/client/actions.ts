'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { setProjectCap } from '@/lib/projects/server';
import { AppError, fail } from '@/lib/types';

const capSchema = z
  .object({ amount: z.number().int().positive().max(100_000_000), mode: z.enum(['hard', 'soft']) })
  .nullable();

/** The API refuses a member (403 `AUTH.ORG_ADMIN_ONLY`): the cap is the organization's money. */
export async function setCapAction(projectId: string, cap: z.infer<typeof capSchema>) {
  const parsed = capSchema.safeParse(cap);
  if (!parsed.success || !z.uuid().safeParse(projectId).success) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid cap'));
  }
  const result = await setProjectCap(projectId, parsed.data);
  if (result.success) revalidatePath('/settings/client');
  return result;
}
