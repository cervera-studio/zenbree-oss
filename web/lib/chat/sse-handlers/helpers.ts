import type { UIToolCall } from '../stream-store-types';

// `appendToolPart` lives one level up at `../message-parts.ts`, next to the
// `appendTextPart` the rAF text ticker uses. Re-exported here so handler files
// keep a single import surface.
export { appendToolPart } from '../message-parts';

/**
 * Locate a tool call to patch. Prefers `toolId` (new events), falls back
 * to matching the last still-running call with the same name.
 */
export function patchCall(
  calls: UIToolCall[],
  data: { toolId?: string; toolName?: string },
  patch: (tc: UIToolCall) => UIToolCall,
): UIToolCall[] {
  const { toolId, toolName } = data;
  let idx = -1;
  if (toolId) {
    idx = calls.findIndex((tc) => tc.id === toolId);
  }
  if (idx === -1 && toolName) {
    idx = calls.findLastIndex(
      (tc) => tc.name === toolName && (tc.status === 'running' || tc.creditsUsed === undefined),
    );
  }
  if (idx === -1) return calls;
  const next = [...calls];
  next[idx] = patch(next[idx] as UIToolCall);
  return next;
}
