import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { AppPage } from '@/components/app/page';
import { ApprovalQueue } from '@/components/campaigns/approval-queue';
import { ApprovalBanner } from '@/components/campaigns/banner';
import { CampaignActivity } from '@/components/campaigns/campaign-activity';
import { CampaignBuilder } from '@/components/campaigns/campaign-builder';
import {
  CampaignHeader,
  CampaignSide,
  CampaignTiles,
  CampaignTopbarActions,
} from '@/components/campaigns/campaign-detail';
import { CampaignPeople } from '@/components/campaigns/campaign-people';
import { ParallelCampaigns } from '@/components/campaigns/parallel-campaigns';
import { PushPrompt } from '@/components/campaigns/push-prompt';
import { LiveVariantTest } from '@/components/campaigns/variant-test';
import { getActivity, getApprovals, getCampaign, getTargets } from '@/lib/campaigns/api';
import { getBuilderPreview, getFieldCatalog } from '@/lib/campaigns/builder-api';
import { getCampaignSenders } from '@/lib/campaigns/senders';
import { isUuid } from '@/lib/config/uuid';
import { getLinkedIn } from '@/lib/integrations/linkedin';
import { getPushConfig } from '@/lib/notifications/push-config';

export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const [t, campaign, approvals, pool] = await Promise.all([
    getTranslations('campaigns'),
    getCampaign(id),
    getApprovals(id),
    getCampaignSenders(id),
  ]);
  if (!campaign.success) {
    if (campaign.error.status === 404) notFound();
    return <p className="text-negative p-6 text-sm">{t('loadError')}</p>;
  }
  // #307: it reads its own project's LinkedIn, whichever client is active.
  const project = campaign.data.projectId;
  const senders = pool.success ? pool.data : null;
  const queue = (
    <ApprovalQueue
      campaignId={id}
      items={approvals.success ? approvals.data.items : []}
      loadFailed={!approvals.success}
      active={campaign.data.status === 'active'}
    />
  );

  if (campaign.data.status === 'draft') {
    const [linkedIn, preview, catalog] = await Promise.all([
      getLinkedIn(project),
      getBuilderPreview(id),
      getFieldCatalog(id),
    ]);
    return (
      <CampaignBuilder
        campaign={campaign.data}
        senders={senders}
        linkedInConnected={linkedIn.success && linkedIn.data.connected}
        preview={preview.success ? preview.data.items : []}
        catalog={catalog.success ? catalog.data : null}
        queue={queue}
      />
    );
  }

  // C3 (#218): a campaign that has left draft is followed, not edited.
  const waiting = campaign.data.awaitingApproval;
  const [targets, push, activity] = await Promise.all([
    getTargets(id),
    getPushConfig(),
    getActivity(id),
  ]);
  return (
    <AppPage
      crumbs={[{ label: t('title'), href: '/campaigns' }, { label: campaign.data.name }]}
      crumbsLabel={t('breadcrumb')}
      topbarActions={<CampaignTopbarActions campaign={campaign.data} />}
      className="phone:pb-28"
    >
      <CampaignHeader campaign={campaign.data} senders={senders} />
      {waiting > 0 && (
        <ApprovalBanner
          total={waiting}
          where={campaign.data.name}
          href={`/campaigns/${id}/approvals`}
        />
      )}
      {campaign.data.status === 'active' && push.publicKey && (
        <PushPrompt publicKey={push.publicKey} />
      )}
      <CampaignTiles campaign={campaign.data} />
      <div className="grid grid-cols-1 items-start gap-4 desktop:grid-cols-[minmax(0,1fr)_21.25rem]">
        <div className="flex min-w-0 flex-col gap-4">
          {queue}
          <CampaignPeople
            campaign={campaign.data}
            targets={targets.success ? targets.data.items : null}
            steps={campaign.data.steps}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <CampaignSide campaign={campaign.data} senders={senders} />
          <LiveVariantTest campaign={campaign.data} />
          <ParallelCampaigns campaign={campaign.data} />
          {/* Outside CampaignSide, which phone hides: on phone it follows the people. */}
          <CampaignActivity campaignId={id} first={activity.success ? activity.data : null} />
        </div>
      </div>
    </AppPage>
  );
}
