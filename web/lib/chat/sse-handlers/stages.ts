import { parseProgress } from '../job-progress';
import type { SSEHandler } from './types';

export const handleStages: SSEHandler<'stages'> = (ctx, data) => {
  const progress = parseProgress(data);
  if (progress) ctx.setChat(ctx.key, (prev) => ({ ...prev, progress }));
  return null;
};
