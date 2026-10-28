import type { SSEHandler } from './types';

/**
 * A TERMINAL code (the loop stopped on a budget, e.g. `max_turns_reached`) is
 * kept as `terminalCode` and rendered localized; the run collected data, so the
 * message is `partial`, not `failed`. Anything else marks the turn failed: the
 * chat draws its own localized notice, since the BE's sentence is English and
 * `content` only mirrors the text parts.
 */
export const handleError: SSEHandler<'error'> = (ctx, data) => {
  const errorCode = data.code;
  const terminalLabel = errorCode ? ctx.labels.terminals[errorCode] : undefined;
  ctx.clearAllToolWatchdogs();
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => ({
    ...msg,
    status: terminalLabel ? ('partial' as const) : ('failed' as const),
    ...(terminalLabel ? { terminalCode: errorCode } : { turnFailed: true }),
    toolCalls: msg.toolCalls.map((tc) =>
      tc.status === 'running' ? { ...tc, status: 'error' as const } : tc,
    ),
  }));
  return null;
};
