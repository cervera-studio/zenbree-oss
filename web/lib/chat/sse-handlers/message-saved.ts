import { applyPlanList } from '../chat-lists';
import { parseQuestion } from '../question';
import type { SSEHandler } from './types';

/** BE promotes the placeholder client id to the real chat_messages.id (+ derived status). */
export const handleMessageSaved: SSEHandler<'message_saved'> = (ctx, data) => {
  const newId = data.messageId;
  const newStatus = data.status;
  const question = parseQuestion(data.question);
  if (!newId) return null;
  // Read fresh rather than relying on ctx.targetId: keeps this handler
  // self-contained and immune to any future change in when the dispatcher
  // pins targetId. Equivalent today since handleSSEEvent is synchronous.
  const run = ctx.run(ctx.key);
  const oldId = run.assistantId;
  run.assistantId = newId;
  ctx.setChat(ctx.key, (prev) => {
    const idx = prev.messages.findLastIndex((m) => m.id === oldId);
    const lists = question?.plan ? applyPlanList(prev.lists, question.plan) : prev.lists;
    const existing = idx === -1 ? undefined : prev.messages[idx];
    if (!existing) return { ...prev, lists };
    const messages = [...prev.messages];
    messages[idx] = {
      ...existing,
      id: newId,
      ...(newStatus !== undefined && { status: newStatus }),
      ...(question && { question }),
    };
    return { ...prev, messages, lists };
  });
  return null;
};
