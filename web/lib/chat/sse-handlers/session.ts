import { advanced } from '../chat-turn';
import { DRAFT_KEY } from '../stream-store-types';
import type { SSEHandler } from './types';

/**
 * `session` event: the BE assigns a real sessionId (and the turn's job id) to
 * a stream. On DRAFT_KEY we write the sid into DRAFT first so the page sees it
 * and updates its URL, then alias the same state under the real sid.
 */
export const handleSession: SSEHandler<'session'> = (ctx, data) => {
  const newSid = data.sessionId;
  const jobId = typeof data.jobId === 'string' ? data.jobId : null;
  if (!newSid) return null;
  ctx.setChat(ctx.key, (prev) => {
    const next = { ...prev, sessionId: newSid, jobId: jobId ?? prev.jobId };
    return jobId ? advanced(next, { type: 'jobStarted', jobId }) : next;
  });
  if (ctx.key === DRAFT_KEY) {
    ctx.migrateKey(DRAFT_KEY, newSid);
    return newSid;
  }
  return null;
};
