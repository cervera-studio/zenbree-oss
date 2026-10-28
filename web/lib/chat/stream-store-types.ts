/**
 * Shared types + constants for ChatStreamStore and the sse-handlers/
 * dispatcher, so handlers can `import type` without the orchestrator class.
 */

import type { AppErrorJSON } from '@/lib/types';
import type { Brief } from './brief';
import type { CampaignDraft } from './campaign';
import type { ChatLists, ListEntity } from './chat-lists';
import type { ChatTurn } from './chat-turn';
import type { JobProgress } from './job-progress';
import type { ToolSummary } from './tool-summary';
import type {
  AgentQuestion,
  ChatMessage,
  MessagePart,
  PlanApproval,
  Source,
  ToolCallStatus,
} from './types';

export const DRAFT_KEY = '__draft__';

/** What a campaign-goal chat adds to the first message (docs/contracts-m1.md, `POST /v1/chat`). */
export type ChatGoal = 'campaign';

export interface SendOptions {
  planApproval?: PlanApproval;
  pick?: number;
  /** Honoured by the API only on the message that creates the session. */
  goal?: ChatGoal;
  /** New sessions only: the key of a stored onboarding segment (a Home welcome card), whose chat the server opens. */
  segment?: string;
  /** The chat's name until the server sends its own; never sent. */
  title?: string;
  /** The server refused the turn before it started (another job runs): the turn left the chat. */
  onRejected?: () => void;
}

export type ChatKey = string;

/**
 * Which shape of mid-turn cut a message survived. `partial`/`empty`: socket
 * dropped with/without words on screen; `timeout`: our own 15 min cap;
 * `resync`: re-attach found no run to rejoin.
 */
export type StreamInterruption = 'partial' | 'empty' | 'timeout' | 'resync';

export interface UIMessage {
  id: string;
  role: 'user' | 'assistant';
  /** Canonical text — mirror of concatenated TextParts. */
  content: string;
  toolCalls: UIToolCall[];
  /** Temporal order of text and tool calls as they arrived. */
  parts: MessagePart[];
  createdAt: Date;
  /** Between `agent_thinking` and the first text token. */
  isWriting?: boolean;
  status?: 'success' | 'partial' | 'failed' | 'uncertain';
  /** The job itself failed (an `error` frame, or a saved `failed` row), not a refused request. */
  turnFailed?: boolean;
  sources?: Source[];
  /** Why the run stopped on an agent-loop terminal (`max_turns_reached`, …). */
  terminalCode?: string;
  /** The SSE connection died mid-turn. Never set on a user abort. */
  interrupted?: StreamInterruption;
  creditsUsed?: number;
  /** The turn paused on `ask_user`: its options render as answer buttons. */
  question?: AgentQuestion;
}

export interface UIToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: string;
  creditsUsed?: number;
  /** Why a failed call stopped when a plan stage code explains it (`no_credits`, `disabled`…). */
  errorCode?: string;
  status: ToolCallStatus;
  summary?: ToolSummary;
  brief?: Brief;
  memorySaved?: string;
  campaign?: CampaignDraft;
}

export interface UseChatLabels {
  networkError: string;
  streamTimeout: string;
  streamInterrupted: string;
  creditsExhausted: string;
  creditsLow: string;
  /** «Ferma» could not reach the server: the job goes on. */
  stopFailed: string;
  upgradeCta: string;
  /** Why a run stopped, keyed by the terminal `code` on the `error` frame. */
  terminals: Record<string, string>;
  /** The text for a refused request, by its code (`useErrorText`). */
  errorText: (error: AppErrorJSON) => string;
}

export interface ChatState {
  messages: UIMessage[];
  sessionId: string | null;
  turn: ChatTurn;
  /** Job of the current / last turn (from the `session` frame). */
  jobId: string | null;
  lists: ChatLists;
  activeEntity: ListEntity | null;
  title: string | null;
  progress: JobProgress | null;
  /** Credits the AI column fills of the chat's lists charged (#239): billed outside the turns. */
  fillCredits: number;
}

export interface StreamRun {
  abort: AbortController | null;
  assistantId: string;
  pendingText: string;
  ticker: number | null;
  /** Last fully-dispatched replay frame id; sent back as `Last-Event-ID`. */
  lastEventId: number;
  /** «Nuova chat» came before the `session` frame: drop the draft's alias once it arrives. */
  releaseOnMigrate: boolean;
}

export function newRun(): StreamRun {
  return {
    abort: null,
    assistantId: '',
    pendingText: '',
    ticker: null,
    lastEventId: 0,
    releaseOnMigrate: false,
  };
}

export interface GlobalState {
  credits: number | null;
  maxCredits: number | null;
}

export interface RecoveredSession {
  messages: ChatMessage[];
  lists: ChatLists;
  jobId: string | null;
  title: string | null;
  progress: JobProgress | null;
}
