import { safeRandomUUID } from '@/lib/config/uuid';
import { parseBrief } from './brief';
import { parseCampaign } from './campaign';
import { parseMemorySaved, parseQuestion } from './question';
import { parseSources } from './sources';
import type { UIMessage, UIToolCall } from './stream-store-types';
import { toolStatusFromWire } from './tool-status';
import { parseToolSummary } from './tool-summary';
import type { ChatMessage, MessagePart } from './types';

/**
 * Backend rows → transcript messages. Text FIRST, then tools: within one
 * persisted turn the text precedes the tool calls it announces.
 */
export function hydrateMessages(dbMessages: ChatMessage[]): UIMessage[] {
  return dbMessages.map((msg) => {
    const toolCalls: UIToolCall[] = (msg.toolCalls ?? []).map((tc) => ({
      id: tc.id || safeRandomUUID(),
      name: tc.name,
      input: tc.input ?? {},
      result: tc.result,
      creditsUsed: tc.creditsUsed,
      errorCode: tc.errorCode,
      // Same mapper as the live stream, so reload and live agree.
      status: toolStatusFromWire(tc.verdict),
      summary: parseToolSummary(tc.summary),
      brief: parseBrief(tc.brief),
      memorySaved: parseMemorySaved(tc.memorySaved),
      campaign: parseCampaign(tc.campaign),
    }));
    const parts: MessagePart[] = [
      ...(msg.content
        ? [{ kind: 'text' as const, id: safeRandomUUID(), content: msg.content }]
        : []),
      ...toolCalls.map((tc) => ({
        kind: 'tool' as const,
        id: safeRandomUUID(),
        toolCallId: tc.id,
      })),
    ];
    const sources = parseSources(msg.sources);
    const question = parseQuestion(msg.question);
    return {
      id: msg.id,
      role: msg.role,
      content: msg.content,
      toolCalls,
      parts,
      createdAt: new Date(msg.createdAt),
      ...(msg.role === 'assistant' && { creditsUsed: msg.creditsUsed }),
      ...(msg.status && { status: msg.status }),
      ...(msg.role === 'assistant' && msg.status === 'failed' && { turnFailed: true }),
      ...(sources && { sources }),
      ...(msg.terminalCode && { terminalCode: msg.terminalCode }),
      ...(question && { question }),
    };
  });
}
