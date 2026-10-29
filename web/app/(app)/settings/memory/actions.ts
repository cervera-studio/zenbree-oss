'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { getSession } from '@/lib/auth-token';
import { saveMemory } from '@/lib/memory';
import { AppError, fail } from '@/lib/types';

const memorySchema = z.object({ body: z.string().max(8000), locked: z.boolean() });

export async function saveMemoryAction(body: string, locked: boolean) {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const parsed = memorySchema.safeParse({ body, locked });
  if (!parsed.success) {
    return fail(new AppError('MEMORY.TOO_LONG', 'Memory is limited to 8000 characters'));
  }
  const result = await saveMemory(parsed.data.body, parsed.data.locked);
  if (result.success) revalidatePath('/settings/memory');
  return result;
}
