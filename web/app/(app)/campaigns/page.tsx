import { ChartColumn } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { CampaignsEmpty } from '@/components/campaigns/campaigns-empty';
import { CampaignsIndex } from '@/components/campaigns/campaigns-index';
import { NewCampaignDialog } from '@/components/campaigns/new-campaign-dialog';
import { SwitchClientOnMount } from '@/components/campaigns/switch-client-on-mount';
import { buttonVariants } from '@/components/ui/button';
import { getCampaigns, getPeopleLists, otherClientOfPeopleList } from '@/lib/campaigns/api';
import { toTab } from '@/lib/campaigns/list-view';
import { getLinkedIn } from '@/lib/integrations/linkedin';
import { getMailboxes } from '@/lib/integrations/mailboxes';

export default async function CampaignsPage({
  searchParams,
}: {
  searchParams: Promise<{ list?: string; status?: string }>;
}) {
  // `?list=`: a list's «Avvia campagna» (#171) preselects it.
  const { list, status } = await searchParams;
  const [t, reports, campaigns, lists] = await Promise.all([
    getTranslations('campaigns'),
    getTranslations('reports'),
    getCampaigns(),
    getPeopleLists(),
  ]);
  const clientToSwitchTo =
    list && !lists.some((l) => l.id === list) ? await otherClientOfPeopleList(list) : null;

  return (
    <div className="flex min-h-full flex-col">
      <AppPage
        crumbs={[{ label: t('title') }]}
        inProject
        crumbsLabel={t('breadcrumb')}
        topbarActions={
          clientToSwitchTo ? (
            <SwitchClientOnMount client={clientToSwitchTo} />
          ) : (
            <NewCampaignDialog
              lists={lists.map(({ id, name }) => ({ id, name }))}
              defaultListId={list}
            />
          )
        }
        title={t('title')}
        meta={t('subtitle')}
        // In the page head, not the topbar: on the phone the topbar has no room left beside the
        // breadcrumb and the new-campaign button.
        actions={
          <Link
            href="/campaigns/report"
            className={buttonVariants({ variant: 'outline', className: 'phone:h-11' })}
          >
            <ChartColumn aria-hidden />
            {reports('link')}
          </Link>
        }
        className="flex-1"
      >
        {!campaigns.success ? (
          <p className="text-negative text-sm">{t('loadError')}</p>
        ) : campaigns.data.campaigns.length === 0 ? (
          <Empty listName={lists[0]?.name ?? null} />
        ) : (
          <CampaignsIndex campaigns={campaigns.data.campaigns} tab={toTab(status)} />
        )}
      </AppPage>
    </div>
  );
}

async function Empty({ listName }: { listName: string | null }) {
  const [mailboxes, linkedin] = await Promise.all([getMailboxes(), getLinkedIn()]);
  const mailbox = mailboxes.success ? mailboxes.data.mailboxes.find((m) => m.connected) : undefined;
  return (
    <CampaignsEmpty
      list={listName}
      mailbox={mailbox ? (mailbox.accountLabel ?? mailbox.provider) : null}
      linkedin={
        linkedin.success && linkedin.data.connected ? (linkedin.data.accountLabel ?? '') : null
      }
    />
  );
}
