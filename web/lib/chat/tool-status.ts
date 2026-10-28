import type { ToolCallStatus } from './types';

/**
 * The one place the backend's failure vocabulary becomes `ToolCallStatus`.
 *
 * Three words arrive from two producers, and they are a different alphabet from
 * ours on purpose — `API/app/chat/sse/event_stream.py` says so where it
 * stores the persisted one: "Not named `status` because this dict is persisted
 * and the FE's ToolCall.status already means running / done / error."
 *
 * - live stream, normal path: `status: 'success' | 'failed'`
 * - live stream, the #1515 loop that closes announced-but-never-dispatched
 *   calls after a mid-stream provider failure: `status: 'error'`
 * - reload: `verdict: 'success' | 'failed'`, written only for a result that was
 *   actually classified
 *
 * Absent maps to `done`, not `error`: rows persisted before the verdict existed,
 * and any producer that omits the field, must keep rendering as they do today
 * rather than turning a wall of old sessions red.
 *
 * Both callers go through here so the live pill and the reloaded pill cannot
 * disagree — `sse-handlers/error.ts` names that failure mode ("It would also
 * disagree with the reload") for the neighbouring case.
 */
const FAILED_WIRE_WORDS: ReadonlySet<string> = new Set(['failed', 'error']);

export function toolStatusFromWire(wire: unknown): ToolCallStatus {
  return typeof wire === 'string' && FAILED_WIRE_WORDS.has(wire) ? 'error' : 'done';
}
