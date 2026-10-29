'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { issueApiKey, revokeApiKey } from '@/lib/api-keys';
import { getSession } from '@/lib/auth-token';
import { AppError, fail } from '@/lib/types';

const PATH = '/settings/api-keys';
const labelSchema = z.string().trim().min(1).max(120);
const idSchema = z.string().uuid();

export async function createApiKeyAction(label: string) {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const parsed = labelSchema.safeParse(label);
  if (!parsed.success) {
    return fail(
      new AppError('API_KEY.INVALID_LABEL', 'Enter a label between 1 and 120 characters'),
    );
  }
  const result = await issueApiKey(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}

export async function revokeApiKeyAction(id: string) {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const parsed = idSchema.safeParse(id);
  if (!parsed.success) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid API key id'));
  }
  const result = await revokeApiKey(parsed.data);
  if (result.success) revalidatePath(PATH);
  return result;
}
