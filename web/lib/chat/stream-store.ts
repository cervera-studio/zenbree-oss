/**
 * ChatStreamStore — per-chat SSE stream lifecycle, outside React so streams
 * survive chat switching. Each chat key owns its rendered state in the Zustand
 * store (chat-store.ts) and the transport state of its live stream in a
 * `StreamRun`. Copied from the earlier implementation and trimmed: no attachments, variants,
 * regenerate, workspaces or tiers.
 */

import type { StoreApi } from 'zustand/vanilla';
import { toast } from '@/components/ui/sonner';
import { safeRandomUUID } from '@/lib/config/uuid';
import { type Logger, logger } from '@/lib/logger';
import type { ChatLists } from './chat-lists';
import {
  type ChatStoreReader,
  type ChatStoreState,
  canonicalKey,
  chatOf,
  createChatStore,
  draftReleased,
  EMPTY_CHAT,
  migrated,
  withChat,
  withGlobal,
} from './chat-store';
import { advanced, jobNamedAfterStop, nextTurn, turnLive } from './chat-turn';
import type { JobProgress } from './job-progress';
import { RECONNECT_TRIES, reconnectDelay } from './reconnect';
import { mayStartTurn } from './send-credits';
import { hydrateMessages } from './session-loader';
import { handleSSEEvent as dispatchSSEEvent } from './sse-handlers';
import { askStop } from './stop-job';
import { streamSSE } from './stream-request';
import {
  type ChatKey,
  type ChatState,
  DRAFT_KEY,
  type GlobalState,
  newRun,
  type RecoveredSession,
  type SendOptions,
  type StreamRun,
  type UIMessage,
  type UseChatLabels,
} from './stream-store-types';
import { flushPendingText, startTextTicker } from './text-ticker';
import { ToolWatchdog } from './tool-watchdog';
import { emptyAssistant, userMessage } from './turn-messages';
import type { ChatMessage, SSEEventType, StageRetry } from './types';

export class ChatStreamStore {
  private readonly store: StoreApi<ChatStoreState> = createChatStore();
  private readonly runs = new Map<ChatKey, StreamRun>();
  private readonly watchdog: ToolWatchdog;
  private readonly recoverSession: (sessionId: string) => Promise<RecoveredSession | null>;
  private readonly cancelJob: (jobId: string) => Promise<string | null>;
  private readonly logger: Logger;
  private reattachTimers = new Map<ChatKey, ReturnType<typeof setTimeout>>();
  private reattachTries = new Map<ChatKey, number>();
  private lastLabels = new Map<ChatKey, UseChatLabels>();
  private readonly onOnline = () => this.retryNow();

  constructor(
    opts: {
      recoverSession?: (sessionId: string) => Promise<RecoveredSession | null>;
      /** `POST /v1/jobs/{id}/cancel`: the job's status after the request, null when it failed. */
      cancelJob?: (jobId: string) => Promise<string | null>;
      logger?: Logger;
    } = {},
  ) {
    this.recoverSession = opts.recoverSession ?? (async () => null);
    this.cancelJob = opts.cancelJob ?? (async () => null);
    this.logger = opts.logger ?? logger;
    if (typeof window !== 'undefined') window.addEventListener('online', this.onOnline);
    this.watchdog = new ToolWatchdog({
      updateAssistant: (key, targetId, updater) => this.updateAssistant(key, targetId, updater),
    });
  }

  get state(): ChatStoreReader {
    return this.store;
  }

  getChat(key: ChatKey): ChatState {
    return chatOf(this.store.getState(), key);
  }

  run(key: ChatKey): StreamRun {
    const canonical = canonicalKey(this.store.getState(), key);
    let run = this.runs.get(canonical);
    if (!run) {
      run = newRun();
      this.runs.set(canonical, run);
    }
    return run;
  }

  getGlobal(): GlobalState {
    return this.store.getState().global;
  }

  setChat(key: ChatKey, updater: (prev: ChatState) => ChatState): void {
    this.store.setState((s) => withChat(s, key, updater), true);
  }

  setGlobal(updater: (prev: GlobalState) => GlobalState): void {
    this.store.setState((s) => withGlobal(s, updater), true);
  }

  /**
   * Rename the chat at `oldKey` to `newKey`, keeping `oldKey` as a live alias
   * (a *name* for the chat, not a copy): the page switches to `newKey` only
   * once its URL update commits, and until then reads under the draft key
   * must keep seeing live events.
   */
  migrateKey(oldKey: ChatKey, newKey: ChatKey): void {
    const prev = this.store.getState();
    const next = migrated(prev, oldKey, newKey);
    if (next === prev) return;
    const run = this.runs.get(oldKey);
    if (run) this.runs.set(newKey, run);
    this.runs.delete(oldKey);
    this.store.setState(next, true);
    if (run?.releaseOnMigrate) this.clearDraftAfterMigration(newKey);
  }

  /** Break the DRAFT alias this stream installed, so a new chat starts clean. */
  private clearDraftAfterMigration(currentKey: ChatKey): void {
    this.store.setState((s) => draftReleased(s, currentKey), true);
  }

  /** Seed the live balance from a server-rendered baseline; SSE `credits` then updates it. */
  seedCredits(remaining: number): void {
    this.setGlobal((g) => {
      const maxCredits =
        g.maxCredits === null || remaining > g.maxCredits ? remaining : g.maxCredits;
      if (g.credits === remaining && g.maxCredits === maxCredits) return g;
      return { ...g, credits: remaining, maxCredits };
    });
  }

  /**
   * «Ferma». A live turn with a job asks the server to stop it and keeps the stream open: the job
   * closes it with `error` (`cancelled`), `message_saved` and `done`. Before the `session` frame it
   * waits for the job that frame names; with no live stream it only lets go of the stream.
   */
  stop(key: ChatKey, labels?: UseChatLabels): void {
    if (this.getChat(key).turn.phase === 'stopping') return;
    this.setChat(key, (prev) => advanced(prev, { type: 'stopPressed' }));
    const turn = this.getChat(key).turn;
    if (turn.phase !== 'stopping') {
      this.clearReattach(key);
      this.run(key).abort?.abort();
      return;
    }
    if (turn.jobId) askStop(this.cancelJob, (u) => this.setChat(key, u), turn.jobId, labels);
  }

  /** «Nuova chat» (#686): a first turn still streaming keeps its own chat; the draft empties. */
  startNewChat(): void {
    const { turn, sessionId } = this.getChat(DRAFT_KEY);
    if (!turnLive(turn)) this.reset(DRAFT_KEY);
    else if (sessionId) this.clearDraftAfterMigration(sessionId);
    else this.run(DRAFT_KEY).releaseOnMigrate = true;
  }

  reset(key: ChatKey): void {
    this.clearReattach(key);
    const run = this.run(key);
    run.abort?.abort();
    if (run.ticker !== null) cancelAnimationFrame(run.ticker);
    this.runs.delete(canonicalKey(this.store.getState(), key));
    this.setChat(key, () => EMPTY_CHAT);
  }

  loadSession(
    key: ChatKey,
    id: string,
    dbMessages: ChatMessage[],
    extra: {
      lists?: ChatLists;
      jobId?: string | null;
      title?: string | null;
      progress?: JobProgress | null;
      fillCredits?: number;
    } = {},
  ): void {
    // Never clobber a live stream: its SSE events are authoritative.
    if (turnLive(this.getChat(key).turn)) return;
    this.setChat(key, (prev) => ({
      ...advanced(prev, { type: 'loaded' }),
      sessionId: id,
      messages: hydrateMessages(dbMessages),
      lists: extra.lists ?? {},
      activeEntity: null,
      title: extra.title ?? null,
      jobId: extra.jobId ?? prev.jobId,
      progress: extra.progress ?? null,
      fillCredits: extra.fillCredits ?? 0,
    }));
  }

  /** A fill of one of the chat's lists ended while it was open: what it charged joins the header.
   * ponytail: only the open panel's list is followed; a fill on the other list (or a closed panel)
   * shows after a reload. Follow every list's fill stream from the chat if that gap matters. */
  addFillCredits(key: ChatKey, credits: number): void {
    if (credits > 0)
      this.setChat(key, (prev) => ({ ...prev, fillCredits: prev.fillCredits + credits }));
  }

  send(
    key: ChatKey,
    args: {
      message: string;
      retryStage?: StageRetry;
      labels: UseChatLabels;
      onInterrupted?: (hadPartial: boolean) => void;
    } & SendOptions,
  ): void {
    const { message, retryStage, labels } = args;
    if (!message.trim()) return;
    const current = this.getChat(key);
    if (nextTurn(current.turn, { type: 'sent' }) === current.turn) return;
    if (!mayStartTurn(this.getGlobal(), labels, args.onRejected)) return;

    const trimmed = message.trim();
    const assistantId = safeRandomUUID();
    const controller = new AbortController();
    const sid = current.sessionId;

    this.setChat(key, (prev) => ({
      ...advanced(prev, { type: 'sent' }),
      messages: [...prev.messages, userMessage(trimmed), emptyAssistant(assistantId)],
      progress: retryStage ? prev.progress : null,
      ...(!sid && args.title && { title: args.title }),
    }));
    Object.assign(this.run(key), {
      abort: controller,
      assistantId,
      lastEventId: 0,
      releaseOnMigrate: false,
    });

    let finalKey = key;
    void streamSSE(this.streamHost(), {
      ...args,
      key,
      message: trimmed,
      sid,
      signal: controller.signal,
      onKey: (k) => {
        finalKey = k;
      },
    }).then(() => this.afterStream(finalKey, labels));
  }

  /** Rejoin the running job of this session (`GET …/stream`, replays from `Last-Event-ID`). */
  resume(key: ChatKey, labels: UseChatLabels, onInterrupted?: (hadPartial: boolean) => void): void {
    const current = this.getChat(key);
    const run = this.run(key);
    if (turnLive(current.turn) || !current.sessionId || !run.assistantId) return;
    const controller = new AbortController();
    run.abort = controller;
    this.setChat(key, (prev) => ({
      ...advanced(prev, { type: 'rejoined', jobId: prev.jobId }),
      messages: prev.messages.map((m) =>
        m.id === run.assistantId ? { ...m, interrupted: undefined } : m,
      ),
    }));
    void streamSSE(this.streamHost(), {
      key,
      message: '',
      sid: current.sessionId,
      signal: controller.signal,
      labels,
      transport: 'reattach',
      onInterrupted,
    }).then(() => this.afterStream(key, labels));
  }

  private afterStream(key: ChatKey, labels: UseChatLabels): void {
    this.lastLabels.set(key, labels);
    const chat = this.getChat(key);
    const assistant = chat.messages.find((m) => m.id === this.run(key).assistantId);
    if (chat.turn.phase === 'lost') {
      this.schedule(key, () => {
        if (this.getChat(key).turn.phase === 'lost') this.resume(key, labels);
      });
    } else if (assistant?.interrupted === 'resync') {
      this.schedule(key, () => void this.recover(key, labels));
    } else {
      this.reattachTries.delete(key);
    }
  }

  private nextTry(key: ChatKey): number | null {
    const tries = this.reattachTries.get(key) ?? 0;
    if (tries >= RECONNECT_TRIES) return null;
    this.reattachTries.set(key, tries + 1);
    return tries;
  }

  /** Every retry of a lost or unsure stream waits on the same 1 s / 2 s / 4 s backoff and the same cap. */
  private schedule(key: ChatKey, action: () => void): void {
    this.clearTimer(key);
    if (!this.getChat(key).sessionId) return;
    const tries = this.nextTry(key);
    if (tries === null) return;
    this.reattachTimers.set(
      key,
      setTimeout(() => {
        this.reattachTimers.delete(key);
        action();
      }, reconnectDelay(tries)),
    );
  }

  private clearTimer(key: ChatKey): void {
    const timer = this.reattachTimers.get(key);
    if (timer !== undefined) clearTimeout(timer);
    this.reattachTimers.delete(key);
  }

  private clearReattach(key: ChatKey): void {
    const canonical = canonicalKey(this.store.getState(), key);
    this.clearTimer(canonical);
    this.reattachTries.delete(canonical);
  }

  private attached(key: ChatKey): void {
    this.reattachTries.delete(canonicalKey(this.store.getState(), key));
  }

  private retryNow(): void {
    for (const [key, chat] of Object.entries(this.store.getState().chats)) {
      const labels = this.lastLabels.get(key);
      if (chat.turn.phase !== 'lost' || !labels) continue;
      this.clearReattach(key);
      this.resume(key, labels);
    }
  }

  private async recover(key: ChatKey, labels: UseChatLabels): Promise<void> {
    const sid = this.getChat(key).sessionId;
    if (!sid) return;
    const saved = await this.recoverSession(sid);
    if (!saved) {
      this.setChat(key, (prev) => advanced(prev, { type: 'reloadFailed' }));
      this.schedule(key, () => void this.recover(key, labels));
      return;
    }
    this.loadSession(key, sid, saved.messages, saved);
    this.reattachUnanswered(key, labels);
  }

  /** A «Riprova» or plan approval the server calls stale: no bubble for it, the card refreshes to the server's state. */
  private staleRefresh(key: ChatKey, notice: string): void {
    this.setChat(key, (prev) => ({ ...prev, messages: prev.messages.slice(0, -2) }));
    this.run(key).assistantId = '';
    toast.error(notice);
    const sid = this.getChat(key).sessionId;
    if (!sid) return;
    void this.recoverSession(sid).then((saved) => {
      if (saved) this.loadSession(key, sid, saved.messages, saved);
    });
  }

  dispose(): void {
    if (typeof window !== 'undefined') window.removeEventListener('online', this.onOnline);
    for (const key of [...this.reattachTimers.keys()]) this.clearReattach(key);
  }

  /**
   * A loaded session whose last row is the user's: the job is still running
   * server-side (reload mid-turn). Rejoin it instead of showing a bare question;
   * a 404 (it finished in between) leaves an honest "reload" notice.
   */
  reattachUnanswered(
    key: ChatKey,
    labels: UseChatLabels,
    onInterrupted?: (hadPartial: boolean) => void,
  ): void {
    const current = this.getChat(key);
    if (turnLive(current.turn) || current.messages.at(-1)?.role !== 'user') return;
    const assistantId = safeRandomUUID();
    this.setChat(key, (prev) => ({
      ...prev,
      messages: [...prev.messages, emptyAssistant(assistantId)],
    }));
    Object.assign(this.run(key), { assistantId, lastEventId: 0 });
    this.resume(key, labels, onInterrupted);
  }

  /**
   * Patch the assistant row `targetId`, pinned by the caller at event time:
   * `message_saved` may swap `assistantId` before a late text event lands.
   */
  private updateAssistant(
    key: ChatKey,
    targetId: string,
    updater: (msg: UIMessage) => UIMessage,
  ): void {
    const canonical = canonicalKey(this.store.getState(), key);
    this.setChat(canonical, (prev) => {
      const idx = prev.messages.findLastIndex((m) => m.id === targetId);
      const existing = idx !== -1 ? prev.messages[idx] : undefined;
      if (!existing) {
        this.logger.warn('chat.stream.assistant_target_missing', { key: canonical, targetId });
        return prev;
      }
      const updated = updater(existing);
      if (updated === existing) return prev;
      const messages = [...prev.messages];
      messages[idx] = updated;
      return { ...prev, messages };
    });
  }

  private tickerHost() {
    return { run: this.run.bind(this), updateAssistant: this.updateAssistant.bind(this) };
  }

  private streamHost() {
    return {
      logger: this.logger,
      getChat: (k: ChatKey) => this.getChat(k),
      run: (k: ChatKey) => this.run(k),
      setChat: (k: ChatKey, u: (prev: ChatState) => ChatState) => this.setChat(k, u),
      updateAssistant: (k: ChatKey, t: string, u: (msg: UIMessage) => UIMessage) =>
        this.updateAssistant(k, t, u),
      flushPendingText: (k: ChatKey) => flushPendingText(this.tickerHost(), k),
      clearDraftAfterMigration: (k: ChatKey) => this.clearDraftAfterMigration(k),
      attached: (k: ChatKey) => this.attached(k),
      staleRefresh: (k: ChatKey, notice: string) => this.staleRefresh(k, notice),
      handleSSEEvent: (k: ChatKey, type: SSEEventType, dataStr: string, labels: UseChatLabels) =>
        this.handleSSEEvent(k, type, dataStr, labels),
    };
  }

  private handleSSEEvent(
    key: ChatKey,
    type: SSEEventType,
    dataStr: string,
    labels: UseChatLabels,
  ): ChatKey | null {
    const tickerHost = this.tickerHost();
    const before = this.getChat(key).turn;
    const nextKey = dispatchSSEEvent(key, type, dataStr, labels, {
      setChat: (k, u) => this.setChat(k, u),
      setGlobal: (u) => this.setGlobal(u),
      updateAssistant: (k, t, u) => this.updateAssistant(k, t, u),
      getChat: (k) => this.getChat(k),
      run: (k) => this.run(k),
      migrateKey: (o, n) => this.migrateKey(o, n),
      startTextTicker: (k) => startTextTicker(tickerHost, k),
      flushPendingText: (k) => flushPendingText(tickerHost, k),
      armToolWatchdog: (k, t, id) => this.watchdog.arm(k, t, id),
      clearToolWatchdog: (id) => this.watchdog.clear(id),
      clearAllToolWatchdogs: () => this.watchdog.clearAll(),
    });
    const at = nextKey ?? key;
    const named = jobNamedAfterStop(before, this.getChat(at).turn);
    if (named) askStop(this.cancelJob, (u) => this.setChat(at, u), named, labels);
    return nextKey;
  }
}
