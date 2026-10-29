import { toCamelCase } from '@/lib/http-client/case-transform';
import type { SSEData } from '../event-payloads';
import type { ChatKey, UseChatLabels } from '../stream-store-types';
import type { SSEEventType } from '../types';
import { handleAgentThinking } from './agent-thinking';
import { handleCredits } from './credits';
import { handleDone } from './done';
import { handleError } from './error';
import { handleListUpdated } from './list-updated';
import { handleMessageSaved } from './message-saved';
import { handleSession } from './session';
import { handleStages } from './stages';
import { handleText } from './text';
import { handleTextDropped } from './text-dropped';
import { handleToolResult } from './tool-result';
import { handleToolStart } from './tool-start';
import type { SSEHandler, SSEHandlerContext } from './types';

/**
 * Wire event → handler. A new event = handler file + a row here + its name in
 * `SSE_EVENT_TYPES` (the Record keeps this exhaustive).
 */
const SSE_HANDLERS: { [E in SSEEventType]: SSEHandler<E> } = {
  session: handleSession,
  message_saved: handleMessageSaved,
  text: handleText,
  text_dropped: handleTextDropped,
  agent_thinking: handleAgentThinking,
  tool_start: handleToolStart,
  tool_result: handleToolResult,
  credits: handleCredits,
  list_updated: handleListUpdated,
  stages: handleStages,
  error: handleError,
  done: handleDone,
};

export type SSEHandlerDeps = Omit<SSEHandlerContext, 'key' | 'targetId' | 'labels'>;

/**
 * Parse one `data:` line, pin `targetId` from current state (so events in the
 * same buffer as a `message_saved` still target the pre-swap row), dispatch.
 * Returns the next key on a key migration. Malformed frames are swallowed.
 */
export function handleSSEEvent<E extends SSEEventType>(
  key: ChatKey,
  type: E,
  dataStr: string,
  labels: UseChatLabels,
  deps: SSEHandlerDeps,
): ChatKey | null {
  try {
    const data = toCamelCase<SSEData<E>>(JSON.parse(dataStr));
    const handler = SSE_HANDLERS[type];
    if (!handler) return null;
    const targetId = deps.run(key).assistantId;
    return handler({ ...deps, key, targetId, labels }, data);
  } catch {
    return null;
  }
}
