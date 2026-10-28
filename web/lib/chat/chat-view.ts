import { shallow } from 'zustand/vanilla/shallow';
import type { List } from '@/lib/lists/api';
import { type Brief, type BriefStage, briefStage, latestBrief, markExcluded } from './brief';
import type { ChatList, ChatLists, ListEntity } from './chat-lists';
import { turnLive, turnOffline } from './chat-turn';
import { HIDDEN_TOOLS } from './hidden-tools';
import { RETRYABLE_CODES } from './job-progress';
import type { ChatState, UIMessage, UIToolCall } from './stream-store-types';

export type ChatStatus =
  | 'offline'
  | 'running'
  | 'plan_ready'
  | 'needs_answer'
  | 'step_error'
  | 'all_failed'
  | 'done';

type ListChip =
  | { kind: 'rows'; list: ChatList; count: number }
  | { kind: 'draft'; list: ChatList }
  | { kind: 'empty'; list: ChatList };

export const TAB_ORDER: readonly ListEntity[] = ['person', 'company'];
const CHIP_ORDER: readonly ListEntity[] = ['company', 'person'];

export interface ChatView {
  title: string | null;
  status: ChatStatus | null;
  creditsUsed: number;
  chips: ListChip[];
  draftListId: string | null;
  brief: Brief | null;
  stage: BriefStage;
}

type ChatViewInput = Pick<
  ChatState,
  'messages' | 'lists' | 'title' | 'sessionId' | 'turn' | 'progress' | 'fillCredits'
>;

const TITLE_MAX = 80;

// Python's str.splitlines() breaks, less the ASCII separators \x1c-\x1e that no one types. "\r\n"
// splits twice here, and the empty line between is skipped.
const LINE_BREAK = /[\n\r\v\f\x85\u2028\u2029]/;

/** The API's `chat_title` (api/app/agent/turn_start.py): the first line, cut at a word. */
export function chatTitle(text: string): string {
  const line = (text.split(LINE_BREAK).find((l) => l.trim()) ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ');
  // code points, as the API counts them, so a cut never leaves half an emoji
  const chars = Array.from(line);
  if (chars.length <= TITLE_MAX) return line;
  const word = chars.lastIndexOf(' ', TITLE_MAX - 1);
  return `${chars.slice(0, word >= TITLE_MAX / 2 ? word : TITLE_MAX - 1).join('')}…`;
}

function titleOf(chat: ChatViewInput): string | null {
  if (chat.title) return chat.title;
  const first = chat.messages.find((m) => m.role === 'user');
  return (first && chatTitle(first.content)) || null;
}

// The server saves every turn with a status, so a turn without one was cut or stopped on this
// side while its job may go on. `resync` means the reattach found no live job.
function jobMayStillRun(last: UIMessage): boolean {
  if (last.role === 'user') return true;
  return last.status === undefined && !last.question && last.interrupted !== 'resync';
}

/** The turn the chat stands on. A refused plan approval leaves a failed bubble after the plan; the plan is still open. */
function lastTurn(messages: readonly UIMessage[]): UIMessage | undefined {
  const last = messages.at(-1);
  const prev = messages.at(-2);
  return last?.status === 'failed' && prev?.role === 'assistant' && prev.question ? prev : last;
}

const SEARCH_TOOLS: ReadonlySet<string> = new Set([
  'search_companies',
  'search_people',
  'linkedin_search',
  'web_search',
  'more_rows',
]);

/**
 * Every step the agent ran was a search that failed for a reason a retry can fix. The plan card is
 * not a step: it succeeds before the searches start. A refusal (credits, kill switch) or a failed
 * enrichment or draft keeps its own wording.
 */
export function allSearchesFailed(calls: readonly UIToolCall[]): boolean {
  const steps = calls.filter((c) => !HIDDEN_TOOLS.has(c.name) && c.name !== 'propose_plan');
  return (
    steps.length > 0 &&
    steps.every(
      (c) =>
        SEARCH_TOOLS.has(c.name) &&
        c.status === 'error' &&
        (!c.errorCode || RETRYABLE_CODES.includes(c.errorCode)),
    )
  );
}

function deriveStatus(chat: ChatViewInput): ChatStatus | null {
  const last = lastTurn(chat.messages);
  if (!last) return null;
  if (turnOffline(chat.turn)) return 'offline';
  if (turnLive(chat.turn) || jobMayStillRun(last)) return 'running';
  if (last.question?.plan) return 'plan_ready';
  if (last.question) return 'needs_answer';
  if (allSearchesFailed(last.toolCalls)) return 'all_failed';
  if (
    last.status === 'failed' ||
    last.toolCalls.some((c) => c.status === 'error' && !HIDDEN_TOOLS.has(c.name)) ||
    chat.progress?.stages.some((s) => s.error)
  ) {
    return 'step_error';
  }
  return 'done';
}

function creditsUsed(messages: readonly UIMessage[]): number {
  return messages.reduce((sum, m) => sum + (m.role === 'assistant' ? (m.creditsUsed ?? 0) : 0), 0);
}

function hasRows(list: ChatList | undefined): boolean {
  return (list?.rowCount ?? 0) > 0;
}

function chipOf(list: ChatList, draftListId: string | null): ListChip {
  const count = list.rowCount ?? 0;
  if (count > 0) return { kind: 'rows', list, count };
  return list.id === draftListId ? { kind: 'draft', list } : { kind: 'empty', list };
}

export function deriveChatView(chat: ChatViewInput): ChatView | null {
  if (!chat.sessionId && chat.messages.length === 0) return null;
  const status = deriveStatus(chat);
  const draftListId =
    status === 'plan_ready' ? (lastTurn(chat.messages)?.question?.plan?.listId ?? null) : null;
  return {
    title: titleOf(chat),
    status,
    creditsUsed:
      Math.max(creditsUsed(chat.messages), chat.progress?.creditsSpent ?? 0) + chat.fillCredits,
    chips: CHIP_ORDER.flatMap((e) => {
      const list = chat.lists[e];
      return list ? [chipOf(list, draftListId)] : [];
    }),
    draftListId,
    brief: markExcluded(
      latestBrief(chat.messages),
      status === 'needs_answer' ? lastTurn(chat.messages)?.question?.funnel : undefined,
    ),
    stage: briefStage(status, chat.messages, chat.lists),
  };
}

export function sameView(a: ChatView | null, b: ChatView | null): boolean {
  if (a === null || b === null) return a === b;
  const keys = Object.keys(a) as (keyof ChatView)[];
  return keys.length === Object.keys(b).length && keys.every((k) => sameField(a[k], b[k]));
}

function sameField(x: unknown, y: unknown): boolean {
  if (shallow(x, y)) return true;
  return (
    Array.isArray(x) &&
    Array.isArray(y) &&
    x.length === y.length &&
    x.every((v, i) => shallow(v, y[i]))
  );
}

export type PanelChoice =
  | { kind: 'auto' }
  | { kind: 'open'; entity: ListEntity }
  | { kind: 'closed' };

export function resolvePanel(
  choice: PanelChoice,
  lists: ChatLists,
  activeEntity: ListEntity | null,
  status: ChatStatus | null,
  phone = false,
): ListEntity | null {
  if (choice.kind === 'closed') return null;
  // the steps card is the screen while a run goes; on a phone the panel would cover it
  if (phone && choice.kind === 'auto' && status === 'running') return null;
  if (choice.kind === 'open' && lists[choice.entity]) return choice.entity;
  if (activeEntity && hasRows(lists[activeEntity])) return activeEntity;
  if (status === 'plan_ready' || status === 'needs_answer') return null;
  return TAB_ORDER.find((e) => hasRows(lists[e])) ?? null;
}

export type ListMeta =
  | { kind: 'draft' }
  | { kind: 'base' }
  | { kind: 'running'; delivered: number; remaining: number | null }
  | { kind: 'settled'; delivered: number; total: number };

export function listMeta(
  list: Pick<List, 'id' | 'row_count' | 'delivered_count' | 'target_rows'>,
  entity: ListEntity,
  view: {
    status: ChatStatus | null;
    lists: ChatLists;
    draftListId: string | null;
    activeEntity: ListEntity | null;
  },
): ListMeta {
  if (list.id === view.draftListId && list.row_count === 0) return { kind: 'draft' };
  const writer = view.activeEntity ?? (view.lists.person ? 'person' : 'company');
  const writing = view.status === 'running' && writer === entity;
  if (entity === 'company' && view.lists.person && !writing) return { kind: 'base' };
  const delivered = list.delivered_count;
  if (writing) {
    const remaining = list.target_rows === null ? null : Math.max(list.target_rows - delivered, 0);
    return { kind: 'running', delivered, remaining };
  }
  return { kind: 'settled', delivered, total: list.target_rows ?? list.row_count };
}
