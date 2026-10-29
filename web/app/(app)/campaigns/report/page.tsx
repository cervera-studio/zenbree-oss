import { getTimeZone, getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { CampaignReportTable } from '@/components/reports/campaign-report-table';
import { ReportNotes } from '@/components/reports/report-notes';
import { ReportRange } from '@/components/reports/report-range';
import { getCampaignReport } from '@/lib/reports/api';
import { localDay, parseRange } from '@/lib/reports/range';

export default async function CampaignReportPage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string; to?: string }>;
}) {
  const [params, timeZone, t, campaigns] = await Promise.all([
    searchParams,
    getTimeZone(),
    getTranslations('reports'),
    getTranslations('campaigns'),
  ]);
  // "Today" is the user's day, not the server's: the range and the API both count in their zone.
  const today = localDay(new Date(), timeZone);
  const range = parseRange(params, today);
  const report = await getCampaignReport(range, timeZone);

  return (
    <AppPage
      crumbs={[{ label: campaigns('title'), href: '/campaigns' }, { label: t('title') }]}
      inProject
      crumbsLabel={t('breadcrumb')}
      title={t('title')}
      meta={t('subtitle')}
    >
      <ReportRange range={range} today={today} />
      {!report.success ? (
        <p className="text-negative text-sm">{t('loadError')}</p>
      ) : report.data.campaigns.length === 0 ? (
        <p className="text-muted text-ui">{t('empty')}</p>
      ) : (
        <>
          <CampaignReportTable rows={report.data.campaigns} />
          <ReportNotes report={report.data} range={range} timeZone={timeZone} />
        </>
      )}
    </AppPage>
  );
}
