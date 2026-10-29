import { describe, expect, it } from 'vite-plus/test';
import type { components } from '@/lib/api/schema.gen';
import type { SSEPayloads } from './event-payloads';
import { SSE_EVENT_TYPES, type SSEEventType } from './types';

type Generated = components['schemas']['ChatEventPayloads'];

// Key-level only: the API is snake_case and the web store camelCases keys (`toCamelCase`). The mapper for
// whole generated types is issue #596's; this one only names keys.
type CamelKey<S extends string> = S extends `${infer Head}_${infer Tail}`
  ? `${Head}${Capitalize<CamelKey<Tail>>}`
  : S;

type ApiOnly<E extends SSEEventType> = Exclude<
  CamelKey<keyof Generated[E] & string>,
  keyof SSEPayloads[E]
>;
type WebOnly<E extends SSEEventType> = Exclude<
  keyof SSEPayloads[E],
  CamelKey<keyof Generated[E] & string>
>;

// The error type below lists the keys in one place only, and what to edit.
type Parity<E extends SSEEventType> = [ApiOnly<E>, WebOnly<E>] extends [never, never]
  ? true
  : {
      keysInTheApiOnly: ApiOnly<E>;
      keysInTheWebOnly: WebOnly<E>;
      fix: 'edit SSEPayloads in web/lib/chat/event-payloads.ts, or ChatEventPayloads in api/app/agent/event_payloads.py (then pnpm api:types)';
    };

const parity: { [E in SSEEventType]: Parity<E> } = {
  session: true,
  text: true,
  text_dropped: true,
  agent_thinking: true,
  tool_start: true,
  tool_result: true,
  credits: true,
  list_updated: true,
  stages: true,
  error: true,
  message_saved: true,
  done: true,
};

describe('chat event payloads', () => {
  it('are compared with the API for every event name', () => {
    // pnpm typecheck fails at `parity` when a payload key is on one side only
    expect(Object.keys(parity).sort()).toEqual([...SSE_EVENT_TYPES].sort());
  });
});
