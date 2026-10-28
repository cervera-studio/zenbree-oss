import { safeRandomUUID } from '@/lib/config/uuid';
import type { UIMessage } from './stream-store-types';

export function userMessage(content: string): UIMessage {
  return {
    id: safeRandomUUID(),
    role: 'user',
    content,
    toolCalls: [],
    parts: [{ kind: 'text', id: safeRandomUUID(), content }],
    createdAt: new Date(),
  };
}

export function emptyAssistant(id: string): UIMessage {
  return { id, role: 'assistant', content: '', toolCalls: [], parts: [], createdAt: new Date() };
}
