import type { ChatKey, UIMessage } from './stream-store-types';

/**
 * Minimal store surface the watchdog mutates when a fuse fires. Receives
 * an `updateAssistant` so the watchdog can flip a stuck tool to 'error'
 * without reaching into private store internals.
 */
export interface ToolWatchdogHost {
  updateAssistant(key: ChatKey, targetId: string, updater: (msg: UIMessage) => UIMessage): void;
}

/**
 * Per-tool watchdog. `tool_start` arms a fuse; `tool_result` / tool error /
 * stream end all clear it. If the fuse fires, the running tool flips to
 * an error state instead of the spinner staying stuck forever — PR #240
 * incident protection on top of the BE stall fix.
 */
export class ToolWatchdog {
  // ponytail: 120s, not the earlier implementation's 45s — the Zenbree loop has no tool_heartbeat, so the fuse measures duration, not silence.
  private static readonly DEFAULT_MS = 120_000;
  private readonly timeoutMs: number;
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    private readonly host: ToolWatchdogHost,
    timeoutMs: number = ToolWatchdog.DEFAULT_MS,
  ) {
    this.timeoutMs = timeoutMs;
  }

  arm(key: ChatKey, targetId: string | null, toolId: string): void {
    if (!toolId || !targetId) return;
    this.clear(toolId);
    const t = setTimeout(() => {
      this.timers.delete(toolId);
      this.host.updateAssistant(key, targetId, (msg) => ({
        ...msg,
        toolCalls: msg.toolCalls.map((tc) =>
          tc.id === toolId && tc.status === 'running' ? { ...tc, status: 'error' as const } : tc,
        ),
      }));
    }, this.timeoutMs);
    this.timers.set(toolId, t);
  }

  clear(toolId: string): void {
    if (!toolId) return;
    const t = this.timers.get(toolId);
    if (t) {
      clearTimeout(t);
      this.timers.delete(toolId);
    }
  }

  clearAll(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
  }
}
