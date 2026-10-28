import { useTranslations } from 'next-intl';
import { Panel } from '@/components/ui/panel';
import type { PendingSend } from '@/lib/campaigns/api';
import { fullName } from './approval-card';
import { StepIcon } from './step-icon';

/** «Prossimi»: the messages waiting after the one on screen, in the order they come. */
export function ReviewNext({ next }: { next: PendingSend[] }) {
  const t = useTranslations('campaigns.approvals');
  if (next.length === 0) return null;
  return (
    <Panel title={t('queueTitle')} meta={t('queueMeta', { count: next.length })} flush>
      <ul>
        {next.map((item) => (
          <li
            key={item.id}
            className="border-hair flex items-center gap-2.5 border-b px-3 py-2.5 last:border-b-0"
          >
            <StepIcon channel={item.channel} />
            <span className="flex min-w-0 grow flex-col">
              <span className="text-ui-strong text-ink truncate">{fullName(item)}</span>
              {item.companyName && (
                <span className="text-small text-muted truncate">{item.companyName}</span>
              )}
            </span>
            <span className="text-data-sm text-muted shrink-0 font-mono">
              {t('stepShort', { step: item.step })}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
