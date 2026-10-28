import type { ChatLists } from './chat-lists';
import type { ChatStatus } from './chat-view';
import type { UIMessage } from './stream-store-types';
import type { FilterGroup, Funnel } from './types';

const EMAIL_CHOICES = ['required', 'if_found', 'not_needed'] as const;
type EmailChoice = (typeof EMAIL_CHOICES)[number];

/** What the agent understood of the brief: the result of its latest `set_brief` call. */
export interface Brief {
  who: string[];
  companies: string[];
  where: string[];
  signal: string[];
  /** `null` while the user has not decided. */
  email: EmailChoice | null;
  /** Set while the chat stands on an empty search: the field whose rule left no one (#354). */
  excluded?: BriefField;
}

export const BRIEF_FIELDS = ['who', 'companies', 'where', 'signal'] as const;
type BriefField = (typeof BRIEF_FIELDS)[number];

const FIELD_OF_GROUP: Record<FilterGroup, BriefField> = {
  where: 'where',
  what: 'companies',
  size: 'companies',
  money: 'companies',
  signal: 'signal',
  who: 'who',
};

export function markExcluded(brief: Brief | null, funnel: Funnel | undefined): Brief | null {
  const group = funnel?.steps[funnel.excludedIndex]?.group;
  return brief && group ? { ...brief, excluded: FIELD_OF_GROUP[group] } : brief;
}

function chips(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((c): c is string => typeof c === 'string' && !!c) : [];
}

/** `tool_result.brief` / `tool_calls[].brief`; anything that is not an object is no brief. */
export function parseBrief(raw: unknown): Brief | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const r = raw as Record<string, unknown>;
  return {
    who: chips(r.who),
    companies: chips(r.companies),
    where: chips(r.where),
    signal: chips(r.signal),
    email: EMAIL_CHOICES.find((e) => e === r.email) ?? null,
  };
}

/** The latest `set_brief` of the chat replaces the earlier ones. */
export function latestBrief(messages: readonly UIMessage[]): Brief | null {
  return messages.flatMap((m) => m.toolCalls).findLast((c) => c.brief)?.brief ?? null;
}

export const BRIEF_STAGES = ['brief', 'plan', 'search', 'list'] as const;
export type BriefStage = (typeof BRIEF_STAGES)[number];

export function briefStage(
  status: ChatStatus | null,
  messages: readonly UIMessage[],
  lists: ChatLists,
): BriefStage {
  if (status === 'plan_ready') return 'plan';
  const hasRows = Object.values(lists).some((l) => (l?.rowCount ?? 0) > 0);
  const planned = messages.some((m) => m.question?.plan);
  if (status === 'running') return planned || hasRows ? 'search' : 'brief';
  if (status === 'done' || status === 'step_error') return hasRows ? 'list' : 'brief';
  return 'brief';
}
