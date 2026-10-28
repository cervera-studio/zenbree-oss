import { safeRandomUUID } from '@/lib/config/uuid';
import type { UIToolCall } from '../stream-store-types';
import { appendToolPart } from './helpers';
import type { SSEHandler } from './types';

/**
 * BE announces a new tool call. We seed a 'running' UIToolCall, append a
 * temporal tool part so rendering can interleave it with text, and arm
 * the per-tool watchdog (45s fuse) in case tool_result never arrives.
 */
export const handleToolStart: SSEHandler<'tool_start'> = (ctx, data) => {
  if (!data.toolName) return null;
  // Flush any buffered text before inserting the tool part. Without this,
  // text that arrived before the tool but hasn't been committed by the rAF
  // ticker yet ends up in a new TextPart AFTER the ToolPart, visually
  // splitting mid-sentence across tool blocks (e.g. "car" / [tool] / "eer").
  ctx.flushPendingText(ctx.key);
  const tc: UIToolCall = {
    id: data.toolId || safeRandomUUID(),
    name: data.toolName,
    input: data.toolInput ?? {},
    status: 'running',
  };
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => ({
    ...msg,
    toolCalls: [...msg.toolCalls, tc],
    parts: appendToolPart(msg.parts, tc.id),
  }));
  ctx.armToolWatchdog(ctx.key, ctx.targetId, tc.id);
  return null;
};
