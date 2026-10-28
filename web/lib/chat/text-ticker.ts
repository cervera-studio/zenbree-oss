import { appendTextPart } from './message-parts';
import type { ChatKey, StreamRun, UIMessage } from './stream-store-types';

/**
 * Minimal store surface the ticker reads + mutates. Decoupled from
 * ChatStreamStore so the ticker is unit-testable with stubs.
 */
export interface TextTickerHost {
  run(key: ChatKey): StreamRun;
  updateAssistant(key: ChatKey, targetId: string, updater: (msg: UIMessage) => UIMessage): void;
}

/**
 * Drains `pendingText` into the active assistant message on each rAF
 * tick, ~60 frames per buffered chunk so the reveal feels typed rather
 * than dumped. The handle is parked on the run so `reset()` can
 * cancel any in-flight ticker on chat-key switch.
 */
export function startTextTicker(host: TextTickerHost, key: ChatKey): void {
  if (host.run(key).ticker !== null) return;

  const tick = () => {
    const run = host.run(key);
    if (run.pendingText.length === 0) {
      run.ticker = null;
      return;
    }
    const step = Math.max(1, Math.ceil(run.pendingText.length / 60));
    const reveal = run.pendingText.slice(0, step);
    run.pendingText = run.pendingText.slice(step);
    revealText(host, key, reveal);
    run.ticker = requestAnimationFrame(tick);
  };
  host.run(key).ticker = requestAnimationFrame(tick);
}

/**
 * Cancel any running rAF + drain remaining `pendingText` synchronously.
 * Called from `endStream` so the final chunk lands before the turn ends.
 */
export function flushPendingText(host: TextTickerHost, key: ChatKey): void {
  const run = host.run(key);
  if (run.ticker !== null) {
    cancelAnimationFrame(run.ticker);
    run.ticker = null;
  }
  const pending = run.pendingText;
  if (pending.length > 0) {
    run.pendingText = '';
    revealText(host, key, pending);
  }
}

function revealText(host: TextTickerHost, key: ChatKey, chunk: string): void {
  if (!chunk) return;
  // Read assistantId live (not pinned): rAF fires after message_saved may
  // have swapped the id, so the reveal must target whatever the current
  // assistant row is — opposite of the SSE-handler pinning pattern.
  host.updateAssistant(key, host.run(key).assistantId, (msg) => ({
    ...msg,
    content: msg.content + chunk,
    parts: appendTextPart(msg.parts, chunk),
  }));
}
