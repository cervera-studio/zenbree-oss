import { parseToolSummary } from '../tool-summary';
import { patchCall } from './helpers';
import type { SSEHandler } from './types';

/**
 * BE pushes the post-call balance (whole-number remaining) and optionally
 * the cost of the tool call this credit row corresponds to. We pin
 * `maxCredits` to the highest value we've seen so the progress bar in
 * the composer scales to a stable max instead of jittering.
 */
export const handleCredits: SSEHandler<'credits'> = (ctx, data) => {
  if (typeof data.remaining === 'number') {
    const remaining = data.remaining;
    ctx.setGlobal((g) => {
      const maxCredits =
        g.maxCredits === null || remaining > g.maxCredits ? remaining : g.maxCredits;
      if (g.credits === remaining && g.maxCredits === maxCredits) return g;
      return { ...g, credits: remaining, maxCredits };
    });
  }
  if (typeof data.cost === 'number' && data.cost !== 0) {
    const cost = data.cost;
    // A refund frame carries the call's whole updated summary: it replaces the old one, with no merge rule.
    const summary = parseToolSummary(data.summary);
    ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => {
      const calls = patchCall(msg.toolCalls, data, (tc) => ({
        ...tc,
        creditsUsed: (tc.creditsUsed ?? 0) + cost,
        ...(summary && { summary }),
      }));
      return { ...msg, toolCalls: calls, creditsUsed: (msg.creditsUsed ?? 0) + cost };
    });
  }
  return null;
};
