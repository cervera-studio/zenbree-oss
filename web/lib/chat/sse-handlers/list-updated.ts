import { applyListFrame } from '../chat-lists';
import type { SSEHandler } from './types';

export const handleListUpdated: SSEHandler<'list_updated'> = (ctx, data) => {
  if (typeof data.listId !== 'string' || !data.listId) return null;
  const listId = data.listId;
  ctx.setChat(ctx.key, (prev) => {
    const lists = applyListFrame(prev.lists, { ...data, listId });
    if (!lists) return prev;
    const placed = Object.values(lists).find((l) => l?.id === listId);
    return { ...prev, lists, activeEntity: placed?.entity ?? prev.activeEntity };
  });
  return null;
};
