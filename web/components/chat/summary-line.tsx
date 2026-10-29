import { useTranslations } from 'next-intl';
import { type SummaryPart, summaryView, type ToolSummary } from '@/lib/chat/tool-summary';
import { SummaryStateIcon } from './summary-state-icon';

/** The words of a summary's parts, in the interface language. */
export function useSummaryText(): (parts: SummaryPart[]) => string {
  const t = useTranslations('toolSummary');
  return (parts) => parts.map((p) => ('n' in p ? t(p.key, { n: p.n }) : t(p.key))).join(' · ');
}

/** A finished call's summary as its state icon and text: the back office's line, from the chip's one map. */
export function SummaryLine({ summary }: { summary: ToolSummary }) {
  const t = useTranslations('toolSummary');
  const text = useSummaryText();
  const { state, parts } = summaryView(summary);
  return (
    <span className="text-muted flex items-center gap-1.5">
      <SummaryStateIcon state={state} label={t(`state.${state}`)} />
      {text(parts)}
    </span>
  );
}
