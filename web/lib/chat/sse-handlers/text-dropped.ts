import type { UIMessage } from '../stream-store-types';
import type { SSEHandler } from './types';

function withoutTrailingText(msg: UIMessage): UIMessage {
  const tail = msg.parts.at(-1);
  if (tail?.kind !== 'text') return msg;
  return {
    ...msg,
    content: msg.content.slice(0, msg.content.length - tail.content.length),
    parts: msg.parts.slice(0, -1),
  };
}

/**
 * The step whose text just streamed calls a tool, so that text is not the answer (#567). It
 * arrives before the step's first `tool_start`: the trailing text part is that step's text.
 */
export const handleTextDropped: SSEHandler<'text_dropped'> = (ctx) => {
  ctx.flushPendingText(ctx.key);
  ctx.updateAssistant(ctx.key, ctx.targetId, withoutTrailingText);
  return null;
};
