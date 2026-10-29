import type { SSEHandler } from './types';

/** The model is working between steps: show the pulse until the first text token clears it. */
export const handleAgentThinking: SSEHandler<'agent_thinking'> = (ctx) => {
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => ({ ...msg, isWriting: true }));
  return null;
};
