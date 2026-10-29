import type { SSEHandler } from './types';

/**
 * Streamed text chunk. Buffered into the run's `pendingText`, drained by the rAF ticker
 * for a typewriter feel. Clears the writing indicator.
 */
export const handleText: SSEHandler<'text'> = (ctx, data) => {
  if (typeof data.content !== 'string') return null;
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) =>
    msg.isWriting ? { ...msg, isWriting: false } : msg,
  );
  ctx.run(ctx.key).pendingText += data.content;
  ctx.startTextTicker(ctx.key);
  return null;
};
