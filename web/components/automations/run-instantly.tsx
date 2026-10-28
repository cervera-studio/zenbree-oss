import { ExternalLink } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { useErrorText } from '@/i18n/error-text';
import type { AutomationRunDetail } from '@/lib/automations/api';
import type { InstantlyBatch } from '@/lib/integrations/instantly';
import { OUTCOME_TONE, OUTCOMES } from '@/lib/integrations/instantly-campaign';
import { useInstantlyStatus } from './labels';
import { Fact, Facts } from './run-steps';

type Props = {
  instantly: NonNullable<AutomationRunDetail['instantly']>;
  /** The batch the hold became, read now; `unreadable` when that read failed. */
  batch: InstantlyBatch | 'unreadable' | null;
};

/** The people this run holds for Instantly, why they wait, and what their batch did. Counts only:
 *  the run page names no email. */
export function RunInstantly({ instantly, batch }: Props) {
  const t = useTranslations('automations.run.instantly');
  const format = useFormatter();
  const statusText = useInstantlyStatus();
  const { campaign } = instantly;
  return (
    <section aria-labelledby="instantly-title" className="flex max-w-md flex-col gap-2">
      <h2 id="instantly-title" className="text-feature">
        {t('title')}
      </h2>
      <p className="text-small break-words">
        <span className="text-muted">{t('campaign')}</span> {campaign.name} (
        {statusText(campaign.status)})
      </p>
      <Facts>
        <Fact label={instantly.batchId ? t('sent') : t('held')}>
          {format.number(instantly.people, 'credits')}
        </Fact>
      </Facts>
      {instantly.waitingFor && (
        <p className="text-small text-muted">{t(`waiting.${instantly.waitingFor}`)}</p>
      )}
      {batch === 'unreadable' && <p className="text-small text-muted">{t('batchUnreadable')}</p>}
      {batch && batch !== 'unreadable' && <Batch batch={batch} />}
    </section>
  );
}

function Batch({ batch }: { batch: InstantlyBatch }) {
  const t = useTranslations('lists.instantly');
  const errorText = useErrorText();
  const counts = OUTCOMES.filter((o) => (batch.counts[o] ?? 0) > 0);
  return (
    <div role="status" className="flex flex-col gap-2">
      <p className="text-ui-strong break-words">
        {t(`status.${batch.status}`, { name: batch.campaign.name })}
      </p>
      {batch.status === 'failed' && batch.errorCode && (
        <p className="text-negative text-small">{errorText({ code: batch.errorCode })}</p>
      )}
      {counts.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {counts.map((o) => (
            <li key={o}>
              <Badge tone={OUTCOME_TONE[o]}>
                {t('count', { n: batch.counts[o] ?? 0, outcome: t(`outcome.${o}`) })}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      <a
        href={batch.campaign.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent-ink text-ui-strong inline-flex items-center gap-1 self-start phone:min-h-11"
      >
        {t('openInstantly')}
        <ExternalLink aria-hidden className="size-3.5" />
      </a>
    </div>
  );
}
