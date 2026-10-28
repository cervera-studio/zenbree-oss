'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { LABELS, REPLY_BLOCKED, relabel, sendReply } from '@/lib/inbox/api';
import { AppError, fail } from '@/lib/types';

const inputSchema = z.object({ id: z.uuid(), label: z.enum(LABELS) });

export async function relabelAction(id: string, label: string) {
  const parsed = inputSchema.safeParse({ id, label });
  if (!parsed.success) return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
  const result = await relabel(parsed.data.id, parsed.data.label);
  if (result.success) revalidatePath('/inbox');
  return result;
}

const replySchema = z.object({ id: z.uuid(), body: z.string().trim().min(1).max(4000) });

// A send refused for one of these stored nothing, and the thread may now render a note in place of
// the box (a mailbox that just refused its token, #1053): rendering it again would lose the typed text.
// Any other result renders the thread again, since the answer may be stored or even sent.
const BOX_GONE = new Set<string>([...REPLY_BLOCKED, 'INBOX.MAILBOX_ERROR']);

export async function sendReplyAction(id: string, body: string) {
  const parsed = replySchema.safeParse({ id, body });
  if (!parsed.success) return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
  const result = await sendReply(parsed.data.id, parsed.data.body);
  if (result.success || !BOX_GONE.has(result.error.code)) revalidatePath('/inbox');
  return result;
}
