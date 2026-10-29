import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';
import { ApprovalScreen } from '@/components/campaigns/approval-screen';
import { getApprovals, getCampaign } from '@/lib/campaigns/api';
import { isUuid } from '@/lib/config/uuid';

export default async function ApprovalsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const [t, campaign, approvals] = await Promise.all([
    getTranslations('campaigns'),
    getCampaign(id),
    getApprovals(id),
  ]);
  if (!campaign.success) {
    if (campaign.error.status === 404) notFound();
    return <p className="text-negative p-6 text-sm">{t('loadError')}</p>;
  }
  return (
    <ApprovalScreen
      campaign={{ id, name: campaign.data.name }}
      items={approvals.success ? approvals.data.items : []}
      loadFailed={!approvals.success}
    />
  );
}
