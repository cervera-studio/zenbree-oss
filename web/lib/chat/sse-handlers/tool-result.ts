import { parseBrief } from '../brief';
import { parseCampaign } from '../campaign';
import { parseMemorySaved } from '../question';
import { toolStatusFromWire } from '../tool-status';
import { parseToolSummary } from '../tool-summary';
import { patchCall } from './helpers';
import type { SSEHandler } from './types';

export const handleToolResult: SSEHandler<'tool_result'> = (ctx, data) => {
  if (!data.toolName) return null;
  ctx.clearToolWatchdog(data.toolId || '');
  ctx.updateAssistant(ctx.key, ctx.targetId, (msg) => {
    const calls = patchCall(msg.toolCalls, data, (tc) => ({
      ...tc,
      result: data.resultPreview,
      errorCode: data.errorCode,
      // The BE classifies from the FULL result; never derive it from the preview.
      status: toolStatusFromWire(data.status),
      summary: parseToolSummary(data.summary),
      brief: parseBrief(data.brief),
      memorySaved: parseMemorySaved(data.memorySaved),
      campaign: parseCampaign(data.campaign),
    }));
    return { ...msg, toolCalls: calls };
  });
  return null;
};
