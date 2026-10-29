/** Wire + DTO types for the chat. Trimmed from the earlier implementation `lib/chat/types.ts`. */
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';

const TOOL_CALL_STATUSES = ['running', 'done', 'error'] as const;
export type ToolCallStatus = (typeof TOOL_CALL_STATUSES)[number];

/** Row of `GET /v1/chat/sessions/{id}` → `messages[]` (camelCased), with its opaque `tool_calls` typed. */
export type ChatMessage = Omit<Camel<Schema<'ChatMessage'>>, 'toolCalls'> & {
  toolCalls: ToolCall[] | null;
};

export interface QuestionOption {
  label: string;
  /** One line on what the option means for the result or its cost. */
  description: string | null;
  /** Funnel alternatives: how many companies it would find, and the filter group it drops. */
  estimate?: FoundCount;
  drop?: FilterGroup;
}

export const FILTER_GROUPS = ['where', 'what', 'size', 'money', 'signal', 'who'] as const;
export type FilterGroup = (typeof FILTER_GROUPS)[number];

/** Where an empty company or people search lost its rows: one step per filter group, cumulative. */
export interface Funnel {
  steps: { group: FilterGroup; values: string[]; count: number }[];
  /** The first step that left 0. */
  excludedIndex: number;
  /** What the search looked for; funnels saved before #260 are company ones. */
  entity: 'company' | 'person';
}

/** What an `ask_user` turn paused on; the user's next message answers it. */
export interface AgentQuestion {
  question: string;
  options: QuestionOption[];
  /** Set when the turn paused on a plan to approve (`propose_plan`, docs/contracts-m1.md). */
  plan?: AgentPlan;
  funnel?: Funnel;
}

/** `optional` and `unitCost` are absent on plans stored before #212: such a stage cannot be dropped. */
export interface PlanStage {
  tool: string;
  label: string;
  units: number;
  perRow: boolean;
  unitCost?: number;
  optional?: boolean;
  /** Set on the approved copy of the plan: the user dropped this step. */
  dropped?: boolean;
}

export interface AgentPlan {
  listId: string;
  version: number;
  mode: 'build' | 'enrich';
  entity: 'company' | 'person';
  targetRows: number;
  /** `research`: the question an AI column answers per row (#59). */
  columns: {
    key: string;
    label: string;
    required: boolean;
    research?: string;
    description?: string;
  }[];
  stages: PlanStage[];
  creditsFixed: number;
  creditsPerRow: number;
  /** Absent on plans stored before #212. */
  creditsResearchPerRow?: number;
  estimatedCredits: number;
  largeRunCredits: number;
  /** Set once the plan is approved: the row count it was approved for. */
  approvedRows?: number;
}

/** Sent with the message that approves a plan: that turn runs it for `rows` rows. */
export interface PlanApproval {
  listId: string;
  version: number;
  rows: number;
  droppedStages: number[];
}

const SUMMARY_NOUNS = ['company', 'person'] as const;
type SummaryNoun = (typeof SUMMARY_NOUNS)[number];

/** How many of a noun a search found or would find. */
export interface FoundCount {
  count: number;
  noun: SummaryNoun;
}

export function parseFoundCount(raw: unknown): FoundCount | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const { count, noun } = raw as Record<string, unknown>;
  const known = SUMMARY_NOUNS.find((n) => n === noun);
  return typeof count === 'number' && known ? { count, noun: known } : undefined;
}

export interface StageRetry {
  jobId: string;
  stage: number;
}

interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: string;
  creditsUsed?: number;
  /** Why a failed call stopped, when a stage code explains it (`no_credits`, `disabled`…). */
  errorCode?: string;
  /** Persisted `'success' | 'failed'`, a different alphabet from `status` (see tool-status.ts). */
  verdict?: string;
  summary?: unknown;
  /** `set_brief` and `remember` only: typed copies of what the card shows (docs/contracts-m1.md). */
  brief?: unknown;
  memorySaved?: unknown;
  /** `draft_campaign` only. */
  campaign?: unknown;
}

/** Temporal parts of an assistant message: text and tools interleaved as streamed. */
type TextPart = { kind: 'text'; id: string; content: string };
type ToolPart = { kind: 'tool'; id: string; toolCallId: string };
export type MessagePart = TextPart | ToolPart;

export type SourceKind = 'web' | 'scrape' | 'research' | 'social';

export interface Source {
  url: string;
  title: string;
  domain: string;
  favicon?: string;
  sourceKind: SourceKind;
  citedIndices: number[];
  toolId?: string;
  snippet?: string;
  publishedAt?: string;
}

/**
 * Wire event names as the backend sends them (docs/contracts-m1.md, SSE): the
 * the earlier implementation subset the Zenbree loop emits, plus `list_updated`. Anything else is
 * logged and ignored. The the API's list is `CHAT_EVENTS` (api/app/agent/events.py); a test keeps them equal.
 */
export const SSE_EVENT_TYPES = [
  'session',
  'text',
  'text_dropped',
  'agent_thinking',
  'tool_start',
  'tool_result',
  'credits',
  'list_updated',
  'stages',
  'error',
  'message_saved',
  'done',
] as const;
export type SSEEventType = (typeof SSE_EVENT_TYPES)[number];
