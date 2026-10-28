import type { SSEData } from '../event-payloads';
import type {
  ChatKey,
  ChatState,
  GlobalState,
  StreamRun,
  UIMessage,
  UseChatLabels,
} from '../stream-store-types';
import type { SSEEventType } from '../types';

/**
 * Minimal mutation + side-effect surface a single SSE handler needs to
 * apply its event. Built fresh by the dispatcher for each event so handlers
 * stay pure functions of (ctx, data) — easy to unit-test with vi.fn() stubs
 * and impossible to reach across into private store state.
 *
 * `targetId` is captured at handle time so text events arriving in the same
 * SSE buffer as a `message_saved` still target the pre-swap row (the
 * findLastIndex race fix lives here, not inside the handlers).
 */
export interface SSEHandlerContext {
  key: ChatKey;
  targetId: string;
  labels: UseChatLabels;
  setChat(key: ChatKey, updater: (state: ChatState) => ChatState): void;
  setGlobal(updater: (state: GlobalState) => GlobalState): void;
  updateAssistant(key: ChatKey, targetId: string, updater: (msg: UIMessage) => UIMessage): void;
  getChat(key: ChatKey): ChatState;
  run(key: ChatKey): StreamRun;
  migrateKey(oldKey: ChatKey, newKey: ChatKey): void;
  startTextTicker(key: ChatKey): void;
  /** Drain any buffered text into parts synchronously before a tool part is
   * inserted, so text written before a tool renders above it — not below. */
  flushPendingText(key: ChatKey): void;
  armToolWatchdog(key: ChatKey, targetId: string, toolId: string): void;
  clearToolWatchdog(toolId: string): void;
  clearAllToolWatchdogs(): void;
}

/**
 * Pure-ish SSE event handler. Returns a non-null `ChatKey` when the event
 * triggers a key migration (currently only `session` on DRAFT_KEY), so the
 * dispatcher can rebind the stream key. Other handlers return `null`.
 */
export type SSEHandler<E extends SSEEventType = SSEEventType> = (
  ctx: SSEHandlerContext,
  data: SSEData<E>,
) => ChatKey | null;
