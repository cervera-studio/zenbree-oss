import { parseSources } from '../sources';
import type { SSEHandler } from './types';

export const handleDone: SSEHandler<'done'> = (ctx, data) => {
  ctx.clearAllToolWatchdogs();
  const sources = parseSources(data.sources);
  if (!sources) return null;
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => ({ ...msg, sources }));
  return null;
};
