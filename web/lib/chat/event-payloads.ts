import type { SSEEventType } from './types';

/**
 * The data of each chat SSE event as a handler reads it: camelCased by `toCamelCase`, whose input is
 * `ChatEventPayloads` in api/app/agent/event_payloads.py (generated into `lib/api/schema.gen.ts`).
 * `event-payloads.test.ts` fails when a key here and a field there differ. A value a handler parses
 * on its own (`parseToolSummary`, `parseBrief`, ...) is `unknown`.
 */
export interface SSEPayloads {
  session: { sessionId: string; jobId: string };
  text: { content: string };
  text_dropped: Record<string, never>;
  agent_thinking: { turnSeq: number };
  tool_start: { toolId: string; toolName: string; toolInput: Record<string, unknown> };
  tool_result: {
    toolId: string;
    toolName: string;
    resultPreview: string;
    status: 'success' | 'failed';
    summary?: unknown;
    brief?: unknown;
    memorySaved?: unknown;
    campaign?: unknown;
    errorCode?: string;
  };
  credits: {
    toolId: string;
    toolName: string;
    cost: number;
    remaining: number;
    refund?: boolean;
    summary?: unknown;
  };
  list_updated: {
    listId: string;
    entity: string | null;
    rowsAdded?: number;
    rowsUpdated?: number;
    rowCount?: number;
    rowsScored?: number;
  };
  stages: {
    stages: unknown[];
    phase: string | null;
    creditsSpent: number;
    retryStage: number | null;
    listId: string | null;
  };
  error: { message: string; code: string };
  message_saved: {
    messageId: string;
    status: 'success' | 'partial' | 'failed';
    question?: unknown;
  };
  done: { sources: unknown[]; listId: string | null; jobId: string };
}

/** What a handler gets: the wire is not trusted, so every key may be missing and each handler checks it. */
export type SSEData<E extends SSEEventType> = Partial<SSEPayloads[E]>;
