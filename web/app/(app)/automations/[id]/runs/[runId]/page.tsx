import { notFound } from 'next/navigation';
import { getFormatter, getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { RunActions } from '@/components/automations/run-actions';
import { RunCall, RunFacts, RunSummary } from '@/components/automations/run-detail';
import { RunInstantly } from '@/components/automations/run-instantly';
import { RunRefresh } from '@/components/automations/run-refresh';
import { RunSignal } from '@/components/automations/run-signal';
import { RunSteps } from '@/components/automations/run-steps';
import { getSession } from '@/lib/auth-token';
import { getAutomation, getRun } from '@/lib/automations/api';
import { readRunInstantly } from '@/lib/automations/run-instantly';
import { CANCELLABLE } from '@/lib/automations/run-view';
import { getCampaign } from '@/lib/campaigns/api';
import { isUuid } from '@/lib/config/uuid';
import { isOrgAdmin } from '@/lib/org';

export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string; runId: string }>;
}) {
  const { id, runId } = await params;
  if (!isUuid(id) || !isUuid(runId)) notFound();
  const [t, format, automation, run, session, admin] = await Promise.all([
    getTranslations('automations'),
    getFormatter(),
    getAutomation(id),
    getRun(id, runId),
    getSession(),
    isOrgAdmin(),
  ]);
  if (!automation.success || !run.success) {
    const status = automation.success
      ? run.success
        ? 0
        : run.error.status
      : automation.error.status;
    if (status === 404) notFound();
    return (
      <p role="alert" className="text-negative p-6 text-ui">
        {t('loadError')}
      </p>
    );
  }
  const a = automation.data;
  // Approving promotes the run's people at once when the campaign runs: the dialog warns about it.
  const campaignId = a.definition.steps.find((s) => s.type === 'add_to_campaign')?.campaignId;
  // Approving hands the held people to their Instantly campaign: the dialog reads it live too.
  const [campaign, instantly] = await Promise.all([
    campaignId ? getCampaign(campaignId) : null,
    readRunInstantly(run.data, a),
  ]);
  const title = t('run.title', {
    date: format.dateTime(new Date(run.data.createdAt), {
      dateStyle: 'medium',
      timeStyle: 'short',
    }),
  });

  return (
    <AppPage
      crumbs={[
        { label: t('title'), href: '/automations' },
        { label: a.name, href: `/automations/${a.id}` },
        { label: title },
      ]}
      crumbsLabel={t('breadcrumb')}
      title={title}
    >
      <div className="flex flex-col gap-6">
        {(CANCELLABLE.includes(run.data.status) || instantly.moving) && <RunRefresh />}
        <RunFacts run={run.data} automation={a} />
        <RunActions
          run={run.data}
          automation={a}
          viewer={{ id: session?.user.id ?? '', isOrgAdmin: admin }}
          campaignStatus={
            campaign ? (campaign.success ? campaign.data.status : 'unreadable') : 'none'
          }
          instantlyStatus={instantly.onApproval}
        />
        {run.data.delivery && <RunCall delivery={run.data.delivery} />}
        {run.data.signal && <RunSignal signal={run.data.signal} summary={run.data.summary} />}
        <RunSummary run={run.data} />
        {run.data.instantly && (
          <RunInstantly instantly={run.data.instantly} batch={instantly.batch} />
        )}
        <RunSteps steps={run.data.steps} />
      </div>
    </AppPage>
  );
}
