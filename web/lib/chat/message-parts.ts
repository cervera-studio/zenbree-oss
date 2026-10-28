/**
 * Pure helpers for assembling the `MessagePart[]` projection of an
 * assistant turn (text + tool interleaving). Lives at the chat-package
 * root rather than under `sse-handlers/` because the rAF text ticker
 * also consumes it, and a sibling module reaching into a handler
 * directory for a pure utility is a backwards dep.
 */

import { safeRandomUUID } from '@/lib/config/uuid';
import type { MessagePart } from './types';

/**
 * Append a streamed text chunk to the temporal parts list. If the tail is
 * already a TextPart, extend it; otherwise push a new TextPart so ordering
 * between tools and text is preserved across boundaries.
 */
export function appendTextPart(parts: MessagePart[], chunk: string): MessagePart[] {
  const tail = parts[parts.length - 1];
  if (tail?.kind === 'text') {
    const next = parts.slice(0, -1);
    next.push({ ...tail, content: tail.content + chunk });
    return next;
  }
  return [...parts, { kind: 'text', id: safeRandomUUID(), content: chunk }];
}

export function appendToolPart(parts: MessagePart[], toolCallId: string): MessagePart[] {
  return [...parts, { kind: 'tool', id: safeRandomUUID(), toolCallId }];
}
