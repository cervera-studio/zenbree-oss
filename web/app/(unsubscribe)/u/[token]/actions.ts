'use server';

import { z } from 'zod';
import { httpClient } from '@/lib/http-client';
import { AppError, fail } from '@/lib/types';
import { REASONS, type Reason } from './reasons';

// Public and token-only (#172): the API verifies the signature; no session is read or sent.
const tokenSchema = z.string().regex(/^[\w-]{1,600}$/);
const statusSchema = z.looseObject({ status: z.string() });

async function post(token: string, action: '' | '/undo' | '/erasure', reason?: Reason | null) {
  const parsed = tokenSchema.safeParse(token);
  if (!parsed.success) {
    return fail(new AppError('UNSUBSCRIBE.INVALID_LINK', 'Invalid link', 404));
  }
  const query = reason && REASONS.includes(reason) ? `?reason=${reason}` : '';
  return httpClient.post(`/v1/unsubscribe/${parsed.data}${action}${query}`, undefined, {
    schema: statusSchema,
  });
}

export async function unsubscribeAction(token: string, reason: Reason | null) {
  return post(token, '', reason);
}

export async function undoUnsubscribeAction(token: string) {
  return post(token, '/undo');
}

export async function requestErasureAction(token: string) {
  return post(token, '/erasure');
}
