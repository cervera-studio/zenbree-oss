import { useFormatter, useTranslations } from 'next-intl';
import type { AutomationRunDetail } from '@/lib/automations/api';
import { known, NOBODY } from '@/lib/automations/run-view';
import { Fact, Facts } from './run-steps';

export function RunSignal({
  signal,
  summary,
}: {
  signal: NonNullable<AutomationRunDetail['signal']>;
  summary: AutomationRunDetail['summary'];
}) {
  const t = useTranslations('automations.run.signal');
  const format = useFormatter();
  const nobody = summary.taken?.nobody ?? null;
  const settled = summary.signals;
  const givenUp = settled && 'givenUp' in settled ? settled.failedRuns : null;
  return (
    <section aria-labelledby="signal-title" className="flex max-w-md flex-col gap-2">
      <h2 id="signal-title" className="text-feature">
        {t('title')}
      </h2>
      <Facts>
        <Fact label={t('account')}>
          <span className="break-words">{signal.accountName ?? t('accountGone')}</span>
        </Fact>
      </Facts>
      {signal.signals.length === 0 ? (
        <p className="text-small text-muted">
          {t(settled && 'givenBack' in settled ? 'givenBack' : 'gone')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {signal.signals.map((s) => (
            <li
              key={`${s.type} ${s.occurredAt} ${s.score} ${s.title}`}
              className="text-small flex items-baseline justify-between gap-3"
            >
              <span className="flex min-w-0 flex-col">
                <span className="text-ink break-words">{s.title ?? t('noTitle')}</span>
                <span className="text-muted">
                  {format.dateTime(new Date(s.occurredAt), { dateStyle: 'medium' })}
                </span>
              </span>
              <span className="text-soft shrink-0 font-mono tabular-nums">
                {t('score', { n: Math.round(s.score) })}
              </span>
            </li>
          ))}
        </ul>
      )}
      {nobody !== null && (
        <p className="text-small text-ink">
          {known(NOBODY, nobody) ? t(`nobody.${nobody}`) : t('nobody.other')}
        </p>
      )}
      {givenUp !== null && (
        <p className="bg-warning-tint text-warning text-small rounded-md px-3 py-2">
          {t('givenUp', { n: givenUp })}
        </p>
      )}
    </section>
  );
}
