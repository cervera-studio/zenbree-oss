import { toast } from '@/components/ui/sonner';
import { isLocale } from '@/i18n/config';
import { responseError } from '@/lib/api/error';
import { API_ROUTES } from '@/lib/config/api-routes';
import { getPublicApiBaseUrl } from '@/lib/config/env';
import { toSnakeCase } from '@/lib/http-client/case-transform';
import type { Logger } from '@/lib/logger';
import { getCurrentProjectId } from '@/lib/projects';
import { getAuthToken } from './auth';
import { advanced, type TurnEvent } from './chat-turn';
import { appendTextPart } from './message-parts';
import {
  type ChatGoal,
  type ChatKey,
  type ChatState,
  DRAFT_KEY,
  type StreamInterruption,
  type StreamRun,
  type UIMessage,
  type UseChatLabels,
} from './stream-store-types';
import { type PlanApproval, SSE_EVENT_TYPES, type SSEEventType, type StageRetry } from './types';

const PUBLIC_API_BASE_URL = getPublicApiBaseUrl();
// Long agent runs (search + enrich + score) are legit; the client is the only cap in the path.
const STREAM_TIMEOUT_MS = 15 * 60 * 1000;
// The API pings every 15s. Nothing for this long = the transport is wedged
// (notably WebKit after a background/bfcache transition), not the agent working.
const STREAM_STALL_TIMEOUT_MS = 45 * 1000;
// How long the reader waits for the server's EOF after `done` before cancelling.
const STREAM_CLOSE_AFTER_DONE_MS = 1000;

function isSSEEventType(value: string): value is SSEEventType {
  return SSE_EVENT_TYPES.some((eventType) => eventType === value);
}

/** Host surface the pipeline mutates: bound store methods, no class import. */
export interface StreamRequestHost {
  logger: Logger;
  getChat(key: ChatKey): ChatState;
  run(key: ChatKey): StreamRun;
  setChat(key: ChatKey, updater: (prev: ChatState) => ChatState): void;
  updateAssistant(key: ChatKey, targetId: string, updater: (msg: UIMessage) => UIMessage): void;
  flushPendingText(key: ChatKey): void;
  clearDraftAfterMigration(key: ChatKey): void;
  attached(key: ChatKey): void;
  staleRefresh(key: ChatKey, notice: string): void;
  handleSSEEvent(
    key: ChatKey,
    type: SSEEventType,
    dataStr: string,
    labels: UseChatLabels,
  ): ChatKey | null;
}

export interface StreamRequestArgs {
  key: ChatKey;
  message: string;
  planApproval?: PlanApproval;
  pick?: number;
  goal?: ChatGoal;
  segment?: string;
  retryStage?: StageRetry;
  sid: string | null;
  signal: AbortSignal;
  labels: UseChatLabels;
  /** `reattach` is a GET against the session's running job. */
  transport?: 'send' | 'reattach';
  /** The key the turn runs under, each time the `session` frame renames it. */
  onKey?: (key: ChatKey) => void;
  /** The stream died mid-turn (never a user abort). Fires at most once. */
  onInterrupted?: (hadPartial: boolean) => void;
  onRejected?: () => void;
}

/**
 * Fetch the SSE response, parse `event:` / `id:` / `data:` framing, route each
 * frame through `handleSSEEvent`, rebind the key when `session` migrates it.
 */
// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: the earlier implementation's pipeline, kept whole so it stays diffable against the source
export async function streamSSE(host: StreamRequestHost, args: StreamRequestArgs): Promise<void> {
  const { message, sid, signal, labels } = args;

  let streamKey = args.key;
  const startedFromDraft = args.key === DRAFT_KEY;
  let didEnd = false;

  const getKey = () => streamKey;
  const getAssistantId = () => host.run(getKey()).assistantId;
  const advance = (event: TurnEvent) => host.setChat(getKey(), (prev) => advanced(prev, event));

  const endStream = () => {
    if (didEnd) return;
    didEnd = true;
    const key = getKey();
    host.flushPendingText(key);
    host.run(key).abort = null;
    advance({ type: 'ended' });
    if (startedFromDraft && key !== DRAFT_KEY) host.clearDraftAfterMigration(key);
  };

  // A graceful EOF without `done` is a cut, not an end.
  let sawDone = false;
  // Last frame handled was `error`: the stop is already explained, don't stack a second story.
  let lastFrameWasError = false;

  /** The turn leaves the chat and its reason shows as a toast. */
  const withdraw = (notice: string) => {
    const assistantId = getAssistantId();
    host.setChat(getKey(), (prev) => {
      const at = prev.messages.findIndex((m) => m.id === assistantId);
      // the withdrawn turn's title leaves with it, so the next message names the new chat
      const kept = sid ? prev : { ...prev, title: null };
      return at < 1
        ? kept
        : { ...kept, messages: prev.messages.filter((_, i) => i < at - 1 || i > at) };
    });
    toast.error(notice);
    endStream();
    args.onRejected?.();
  };

  // A send that fails before its turn runs leaves no turn when a 409 refused it, or when it is a card's with no
  // session: a card's line alone would open a chat with no filters. The server kept no chat of it (a 503 deletes
  // it), except after a stream cut before `session`, when its chat is in the sidebar.
  const leavesNoTurn = (refusal?: { status: number; code: string }) =>
    (args.segment !== undefined && !host.getChat(getKey()).sessionId) ||
    (refusal?.status === 409 && !refusal.code.startsWith('PLAN.'));

  /**
   * Mark the turn as cut, once. Flush first: streamed text sits in
   * `pendingText` until the rAF ticker commits it, and a truncated answer must
   * not report itself as a blank screen.
   */
  const markInterrupted = (fallback: string, forced?: StreamInterruption) => {
    if (leavesNoTurn()) return withdraw(fallback);
    const key = getKey();
    host.flushPendingText(key);
    const assistantId = getAssistantId();
    const target = host.getChat(key).messages.find((m) => m.id === assistantId);
    if (!target || lastFrameWasError || target.interrupted || target.terminalCode) return;
    const hadPartial = Boolean(target.content);
    const kind: StreamInterruption = forced ?? (hadPartial ? 'partial' : 'empty');
    const dropped = kind === 'partial' || kind === 'empty';
    host.updateAssistant(key, assistantId, (msg) => ({
      ...msg,
      content: dropped ? msg.content : msg.content || fallback,
      interrupted: kind,
    }));
    if (dropped) advance({ type: 'dropped' });
    args.onInterrupted?.(hadPartial);
  };

  // The chat renders `parts`, so the text goes in as a part and `content` mirrors it.
  const showText = (msg: UIMessage, text: string): UIMessage => ({
    ...msg,
    content: text,
    parts: appendTextPart(msg.parts, text),
  });
  /** The job is no longer there to rejoin (it ended, or its replay is gone): the saved answer is on the server. */
  const markResync = () => {
    const key = getKey();
    host.flushPendingText(key);
    host.updateAssistant(key, getAssistantId(), (msg) => ({
      ...msg,
      content: msg.content || labels.streamInterrupted,
      interrupted: 'resync',
    }));
    advance({ type: 'resynced' });
  };

  const failWith = (text: string) => {
    host.updateAssistant(getKey(), getAssistantId(), (msg) =>
      msg.content ? msg : { ...showText(msg, text), status: 'failed' },
    );
    endStream();
  };

  const failUnanswered = () => (leavesNoTurn() ? withdraw : failWith)(labels.networkError);

  const isReattach = args.transport === 'reattach';
  const targetUrl =
    isReattach && sid
      ? `${PUBLIC_API_BASE_URL}${API_ROUTES.chat.sessions.stream(sid)}`
      : `${PUBLIC_API_BASE_URL}${API_ROUTES.chat.send}`;

  let token: string | null;
  try {
    token = await getAuthToken();
  } catch (error) {
    host.logger.error('chat.stream.auth_token_failed', { sessionId: sid, error });
    return failUnanswered();
  }

  // A new session goes in the current project (the the API's default one when unset).
  const projectId = sid ? null : getCurrentProjectId();
  // The interface's language, which may differ from the browser's header and from the one saved on the user.
  const uiLocale =
    !isReattach && typeof document !== 'undefined' ? document.documentElement.lang : '';
  const body = isReattach
    ? undefined
    : JSON.stringify(
        toSnakeCase({
          // a segment's chat takes the message the server writes
          ...(!sid && args.segment !== undefined ? { segment: args.segment } : { message }),
          ...(sid && { sessionId: sid }),
          ...(projectId && { projectId }),
          ...(!sid && args.goal && { goal: args.goal }),
          ...(args.planApproval && { planApproval: args.planApproval }),
          ...(args.pick !== undefined && { pick: args.pick }),
          ...(args.retryStage && { retryStage: args.retryStage }),
          ...(isLocale(uiLocale) && { locale: uiLocale }),
        }),
      );

  const doFetch = async (authToken: string | null): Promise<Response> => {
    const lastEventId = host.run(getKey()).lastEventId;
    return fetch(targetUrl, {
      method: isReattach ? 'GET' : 'POST',
      headers: {
        ...(body && { 'Content-Type': 'application/json' }),
        ...(uiLocale && { 'Accept-Language': uiLocale }),
        ...(authToken && { Authorization: `Bearer ${authToken}` }),
        ...(isReattach && lastEventId > 0 ? { 'Last-Event-ID': String(lastEventId) } : {}),
      },
      ...(body !== undefined && { body }),
      signal,
    });
  };

  let response: Response;
  try {
    response = await doFetch(token);
    // Expired JWT: mint a fresh one once and retry.
    if (response.status === 401) response = await doFetch(await getAuthToken(true));
  } catch (error) {
    if (signal.aborted) return;
    host.logger.error('chat.stream.fetch_failed', { sessionId: sid, error });
    if (isReattach) {
      advance({ type: 'dropped' });
      endStream();
      return;
    }
    return failUnanswered();
  }

  if (!response.ok) {
    // No running job to rejoin (it may have just finished): honest reload notice.
    if (isReattach && response.status === 404) {
      markResync();
      endStream();
      return;
    }
    host.logger.warn('chat.stream.http_error', { sessionId: sid, status: response.status });
    if (isReattach) {
      // a transport error is the connection's state, never the answer's content
      advance({ type: 'dropped' });
      endStream();
      return;
    }
    const error = await responseError(response);
    const { code } = error;
    // A stale «Riprova» or plan approval: the server has a newer state, the chat reloads it.
    const stale =
      response.status === 409 &&
      ((args.retryStage && code === 'RETRY.STALE') || (args.planApproval && code === 'PLAN.STALE'));
    if (stale) {
      host.staleRefresh(getKey(), labels.errorText(error));
      endStream();
      return;
    }
    if (leavesNoTurn({ status: response.status, code })) {
      withdraw(labels.errorText(error));
      return;
    }
    const message = labels.errorText(error);
    const assistantId = getAssistantId();
    host.updateAssistant(getKey(), assistantId, (msg) => ({
      ...showText(msg, message),
      status: 'failed',
    }));
    // A refused approval never happened: its "approved" line must not stand in the chat.
    if (args.planApproval) {
      host.setChat(getKey(), (prev) => ({
        ...prev,
        messages: prev.messages.filter(
          (m, i) => !(m.role === 'user' && prev.messages[i + 1]?.id === assistantId),
        ),
      }));
    }
    endStream();
    return;
  }

  if (isReattach) {
    advance({ type: 'attached' });
    host.attached(getKey());
  }
  const reader = response.body?.getReader();
  if (!reader) {
    endStream();
    return;
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let eventType = '';
  let dataStr = '';
  let eventId = '';

  const timeout = setTimeout(() => {
    host.run(getKey()).abort?.abort();
    markInterrupted(labels.streamTimeout, 'timeout');
    endStream();
  }, STREAM_TIMEOUT_MS);

  let stallTimeout: ReturnType<typeof setTimeout> | null = null;
  const armStallTimeout = () => {
    if (stallTimeout !== null) clearTimeout(stallTimeout);
    stallTimeout = setTimeout(() => {
      host.logger.warn('chat.stream.stalled', { sessionId: sid });
      host.run(getKey()).abort?.abort();
      markInterrupted(labels.streamInterrupted);
      endStream();
    }, STREAM_STALL_TIMEOUT_MS);
  };
  armStallTimeout();

  // `done` ends the turn, not the socket's EOF; give the server a second to
  // close (it settles credits right after `done`) before cancelling.
  let closeAfterDone: ReturnType<typeof setTimeout> | null = null;
  const releaseAfterDone = () => {
    clearTimeout(timeout);
    if (stallTimeout !== null) clearTimeout(stallTimeout);
    endStream();
    closeAfterDone = setTimeout(() => {
      void reader.cancel().catch(() => {});
    }, STREAM_CLOSE_AFTER_DONE_MS);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Every byte counts as liveness, including `: ping` comments.
      if (!sawDone) armStallTimeout();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('id:')) {
          eventId = line.slice(3).trim();
        } else if (line.startsWith('data: ')) {
          dataStr = line.slice(6);
        } else if (line.trim() === '' && eventType && dataStr) {
          if (sawDone) continue;
          if (eventType === 'done') sawDone = true;
          lastFrameWasError = eventType === 'error';
          let nextKey: ChatKey | null = null;
          if (isSSEEventType(eventType)) {
            nextKey = host.handleSSEEvent(getKey(), eventType, dataStr, labels);
          } else {
            host.logger.warn('chat.stream.event_unknown', { sessionId: sid, eventType });
          }
          if (nextKey && nextKey !== streamKey) {
            streamKey = nextKey;
            args.onKey?.(nextKey);
          }
          // Advance only after the frame was handled, so a retry asks for it again.
          const parsedEventId = Number.parseInt(eventId, 10);
          if (Number.isSafeInteger(parsedEventId) && parsedEventId > 0) {
            host.run(getKey()).lastEventId = parsedEventId;
          }
          eventType = '';
          dataStr = '';
          eventId = '';
          if (sawDone) releaseAfterDone();
        }
      }
    }
    if (!sawDone && !signal.aborted) {
      if (isReattach) markResync();
      else markInterrupted(labels.streamInterrupted);
    }
  } catch {
    // `!signal.aborted` keeps the user's own Stop from reading as a failure.
    if (!signal.aborted && !sawDone) markInterrupted(labels.streamInterrupted);
  } finally {
    clearTimeout(timeout);
    if (stallTimeout !== null) clearTimeout(stallTimeout);
    if (closeAfterDone !== null) clearTimeout(closeAfterDone);
    reader.releaseLock();
    endStream();
  }
}
