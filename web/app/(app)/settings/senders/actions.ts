'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { setSenderLink, setSenderPaused, setSenderWindow } from '@/lib/integrations/mailboxes';
import { AppError, type ErrorCode, fail, type Result } from '@/lib/types';

const API_UNREACHABLE: ErrorCode = 'INTERNAL.SERVICE_UNAVAILABLE';

function revalidateUnlessApiUnreachable(result: Result<unknown>) {
  if (result.success || result.error.code !== API_UNREACHABLE) revalidatePath('/settings/senders');
}

export async function setSenderPausedAction(
  senderId: string,
  paused: boolean,
): Promise<Result<unknown>> {
  if (!z.uuid().safeParse(senderId).success || typeof paused !== 'boolean') {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
  }
  const result = await setSenderPaused(senderId, paused);
  revalidateUnlessApiUnreachable(result);
  return result;
}

export async function setSenderLinkAction(
  senderId: string,
  projectId: string,
  linked: boolean,
): Promise<Result<unknown>> {
  if (
    !z.uuid().safeParse(senderId).success ||
    !z.uuid().safeParse(projectId).success ||
    typeof linked !== 'boolean'
  ) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
  }
  const result = await setSenderLink(senderId, projectId, linked);
  revalidateUnlessApiUnreachable(result);
  return result;
}

const windowSchema = z
  .object({
    startHour: z.number().int().min(0).max(23),
    endHour: z.number().int().min(1).max(24),
    days: z.array(z.number().int().min(0).max(6)).min(1).max(7),
  })
  .refine((w) => w.startHour < w.endHour);

export async function setSenderWindowAction(
  senderId: string,
  window: z.input<typeof windowSchema>,
): Promise<Result<unknown>> {
  const parsed = windowSchema.safeParse(window);
  if (!z.uuid().safeParse(senderId).success || !parsed.success) {
    return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
  }
  const result = await setSenderWindow(senderId, parsed.data);
  revalidateUnlessApiUnreachable(result);
  return result;
}
