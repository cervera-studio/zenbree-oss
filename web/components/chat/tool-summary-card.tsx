'use client';

import { useTranslations } from 'next-intl';
import type { UIToolCall } from '@/lib/chat/stream-store-types';
import { summaryView } from '@/lib/chat/tool-summary';
import type { ToolCallStatus } from '@/lib/chat/types';
import { useSummaryText } from './summary-line';
import { type CallState, SummaryStateIcon } from './summary-state-icon';
import { toolCallLabel } from './tool-call-label';

const STATUS_STATE: Record<ToolCallStatus, CallState> = {
  running: 'running',
  done: 'done',
  error: 'error',
};

export function ToolSummaryCard({ call }: { call: UIToolCall }) {
  const t = useTranslations('chat');
  const tSummary = useTranslations('toolSummary');
  const text = useSummaryText();
  const name = t.has(`tools.${call.name}` as 'tools.web_search')
    ? t(`tools.${call.name}` as 'tools.web_search')
    : call.name;
  const label = toolCallLabel(call.input);
  // A finished call reads from its summary: a batch that only failed is an ok tool result whose step did not succeed.
  const view = call.status !== 'running' && call.summary ? summaryView(call.summary) : undefined;
  const state = view?.state ?? STATUS_STATE[call.status];
  const found = view ? text(view.parts) : call.status === 'error' ? tSummary('state.error') : null;

  return (
    <div className="border-hair bg-raised flex min-h-8.5 items-center gap-2 overflow-hidden rounded-md border px-3 whitespace-nowrap phone:min-h-11">
      <SummaryStateIcon state={state} label={tSummary(`state.${state}`)} />
      <span className="text-ui-strong shrink-0">{name}</span>
      {label && (
        <span className="text-data-sm text-muted min-w-0 flex-1 truncate font-mono">{label}</span>
      )}
      {found && (
        <span className="text-data-sm text-muted ml-auto text-right font-mono tabular-nums">
          {found}
        </span>
      )}
    </div>
  );
}
