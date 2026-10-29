import { ArrowUpRight, Send } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import type { CampaignDraft } from '@/lib/chat/campaign';

export function CampaignDraftCard({ campaign }: { campaign: CampaignDraft }) {
  const t = useTranslations('chat.campaign');
  return (
    <Link
      href={`/campaigns/${campaign.id}`}
      className="bg-ground hover:border-accent-ink hover:bg-accent-tint focus-visible:border-accent-ink focus-visible:outline-accent flex items-center gap-3 rounded-lg border px-3.5 py-3 transition-colors focus-visible:outline-2 phone:min-h-11"
    >
      <span className="bg-accent-tint text-accent-ink flex size-8 shrink-0 items-center justify-center rounded-lg">
        <Send className="size-4" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm font-semibold">{t('title')}</span>
        <span className="text-muted text-xs-plus truncate">
          {t('detail', { name: campaign.name, count: campaign.targetCount })}
        </span>
      </span>
      <span className="text-accent-ink flex shrink-0 items-center gap-1 text-sm font-semibold">
        {t('open')}
        <ArrowUpRight className="size-3.5" />
      </span>
    </Link>
  );
}
