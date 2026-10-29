import { useFormatter, useTranslations } from 'next-intl';
import { Panel } from '@/components/ui/panel';
import type { AutomationSignal } from '@/lib/automations/api';
import { useWhen } from './labels';
import { Fact, Facts } from './run-steps';

export function SignalSetup({ signal }: { signal: AutomationSignal }) {
  const t = useTranslations('automations.signal');
  const format = useFormatter();
  const when = useWhen();
  return (
    <Panel title={t('title')}>
      <div className="flex max-w-md flex-col gap-3">
        <Facts>
          <Fact label={t('watchlist')}>
            {signal.watchlistId === null ? (
              <span className="text-warning">{t('deleted')}</span>
            ) : (
              <span className="break-words">{signal.watchlistName}</span>
            )}
          </Fact>
          <Fact label={t('minScore')}>{format.number(signal.minScore, 'credits')}</Fact>
          <Fact label={t('since')}>{when(signal.since)}</Fact>
        </Facts>
        <p className="text-small text-muted">{t('rule')}</p>
        {signal.watchlistId === null && (
          <p className="text-small text-warning">{t('deletedText')}</p>
        )}
      </div>
    </Panel>
  );
}
