import { useFormatter, useTranslations } from 'next-intl';
import type { CampaignReport } from '@/lib/reports/api';
import { localDay, type Range } from '@/lib/reports/range';

/** What the numbers mean, under the table. The Instantly line shows only when the range reaches
 * past the last complete read of Instantly, so its numbers there can still grow. */
export function ReportNotes({
  report,
  range,
  timeZone,
}: {
  report: CampaignReport;
  range: Range;
  timeZone: string;
}) {
  const t = useTranslations('reports.notes');
  const format = useFormatter();
  const instantly = report.campaigns.some((row) => row.sender === 'instantly')
    ? report.instantly
    : null;
  const completeUntil = instantly?.completeUntil ? new Date(instantly.completeUntil) : null;
  return (
    <ul className="text-muted text-small flex flex-col gap-1">
      <li>{t('people')}</li>
      <li>{t('before')}</li>
      <li>{t('positiveBefore')}</li>
      <li>
        <span aria-hidden>* </span>
        {t('unknown')}
      </li>
      {report.campaigns.some((row) => row.variants.length > 0) && (
        <>
          <li>{t('variants')}</li>
          <li>{t('variantGroups')}</li>
        </>
      )}
      {instantly && !completeUntil && <li className="text-warning">{t('instantlyPending')}</li>}
      {completeUntil && localDay(completeUntil, timeZone) <= range.to && (
        <li className="text-warning">
          {t('instantlyPartial', {
            at: format.dateTime(completeUntil, { dateStyle: 'medium', timeStyle: 'short' }),
          })}
        </li>
      )}
    </ul>
  );
}
