import { Send } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { StatusDot, type Tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { HomeLive } from '@/lib/home/live';
import type { HomeOverview } from '@/lib/home/overview';
import { cn } from '@/lib/utils';
import { GoLink, HomePanel, ROW } from './home-panel';
import { popClass } from './home-pulse';

type Campaign = HomeOverview['campaigns'][number];

const SHOWN_ON_PHONE = 2;

const STATUS_TONE = {
  active: 'positive',
  paused: 'warning',
} satisfies Record<Campaign['status'], Tone>;

const CHANNEL_GLYPH = {
  email: '@',
  linkedin: 'in',
  hubspot: 'hs',
} satisfies Record<Campaign['channels'][number], string>;

function CampaignRow({
  campaign,
  index,
  pulse,
}: {
  campaign: Campaign;
  index: number;
  pulse?: number;
}) {
  const t = useTranslations('chat.home.data.campaigns');
  const format = useFormatter();
  const channels = campaign.channels.map((c) => t(`channels.${c}`));
  return (
    <li
      className={cn(
        ROW,
        'flex-wrap gap-y-1 py-1.5 phone:py-2',
        index >= SHOWN_ON_PHONE && 'phone:hidden',
      )}
    >
      <Link
        href={`/campaigns/${campaign.id}`}
        className="text-ui-strong min-w-40 flex-1 truncate hover:underline phone:basis-full"
      >
        {campaign.name}
      </Link>
      <span role="img" aria-label={t('channelsLabel', { list: channels.join(', ') })}>
        <span aria-hidden className="text-data-sm text-muted flex gap-1 font-mono">
          {campaign.channels.map((c) => (
            <span
              key={c}
              className="bg-hair inline-flex h-4.5 min-w-5 items-center justify-center rounded-sm px-1"
            >
              {CHANNEL_GLYPH[c]}
            </span>
          ))}
        </span>
      </span>
      <StatusDot tone={STATUS_TONE[campaign.status]}>{t(`status.${campaign.status}`)}</StatusDot>
      <span className="text-data text-muted flex gap-3 font-mono tabular-nums">
        <span>
          {format.number(campaign.sent, 'credits')} {t('sent', { count: campaign.sent })}
        </span>
        <span>
          <b key={pulse} className={cn('text-ink inline-block font-normal', popClass(pulse))}>
            {format.number(campaign.replies, 'credits')}
          </b>{' '}
          {t('replies', { count: campaign.replies })}
        </span>
      </span>
      <span className="text-data text-ink w-12 text-right font-mono tabular-nums">
        {campaign.sent > 0
          ? format.number(campaign.replies / campaign.sent, {
              style: 'percent',
              maximumFractionDigits: 1,
            })
          : '—'}
      </span>
    </li>
  );
}

function CampaignCta({ hint }: { hint: HomeOverview['campaignHint'] }) {
  const t = useTranslations('chat.home.data.campaigns');
  return (
    <div className="flex flex-col items-start gap-2 px-3 py-4">
      <strong className="text-ui-strong">{t('emptyTitle')}</strong>
      <p className="text-ui text-muted">
        {hint ? t('emptyHint', { name: hint.name, count: hint.verifiedEmails }) : t('emptyGeneric')}
      </p>
      <Button asChild size="sm">
        <Link href={hint ? `/campaigns?list=${hint.listId}` : '/campaigns'}>
          <Send aria-hidden />
          {t('create')}
        </Link>
      </Button>
    </div>
  );
}

export function HomeCampaigns({
  campaigns,
  hint,
  pulses,
}: {
  campaigns: Campaign[];
  hint: HomeOverview['campaignHint'];
  pulses: HomeLive['pulses'];
}) {
  const t = useTranslations('chat.home.data.campaigns');
  return (
    <HomePanel title={t('title')} action={<GoLink href="/campaigns">{t('all')}</GoLink>}>
      {campaigns.length === 0 ? (
        <CampaignCta hint={hint} />
      ) : (
        <ul className="flex flex-col">
          {campaigns.map((c, i) => (
            <CampaignRow key={c.id} campaign={c} index={i} pulse={pulses[`campaign:${c.id}`]} />
          ))}
        </ul>
      )}
    </HomePanel>
  );
}
