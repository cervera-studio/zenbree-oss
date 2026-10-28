import { Inbox, Mail } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useLocale, useTranslations } from 'next-intl';
import { StatusDot, type Tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { KpiStrip } from '@/components/ui/kpi';
import { Panel } from '@/components/ui/panel';
import type { Campaign } from '@/lib/campaigns/api';
import { sendDays } from '@/lib/campaigns/detail-view';
import type { CampaignSenders } from '@/lib/campaigns/senders';
import { PauseButton, SendersDialog, StepsDialog } from './campaign-controls';
import { CampaignHead } from './campaign-head';
import { StepIcon } from './step-icon';

const TONE: Record<Campaign['status'], Tone> = {
  active: 'positive',
  draft: 'neutral',
  paused: 'warning',
  completed: 'accent',
  stopped: 'neutral',
};

const pickedLabels = (senders: CampaignSenders | null) =>
  (senders?.senders ?? [])
    .filter((s) => s.picked)
    .map((s) => ({ id: s.id, label: s.accountLabel ?? s.provider }));

/** The topbar buttons of a campaign: the replies link and pause. */
export function CampaignTopbarActions({ campaign }: { campaign: Campaign }) {
  const t = useTranslations('campaigns');
  const format = useFormatter();
  const unread = Number(campaign.unreadReplies);
  return (
    <div className="flex items-center gap-2">
      <Button asChild variant="outline" size="sm" className="phone:h-11">
        <Link href={`/inbox?campaign=${campaign.id}`}>
          <Inbox />
          <span className="phone:sr-only">{t('detail.replies')}</span>
          {unread > 0 && (
            <span
              role="img"
              aria-label={t('detail.unreadReplies', { count: unread })}
              className="bg-accent text-on-accent flex h-4.5 min-w-4.5 items-center justify-center rounded-full px-1 text-data-sm font-bold tabular-nums"
            >
              {format.number(unread, 'credits')}
            </span>
          )}
        </Link>
      </Button>
      <PauseButton campaign={campaign} />
    </div>
  );
}

/** C3 head: the name and status, then sender, window and length, with «Vedi gli step» on the right. */
export function CampaignHeader({
  campaign,
  senders,
}: {
  campaign: Campaign;
  senders: CampaignSenders | null;
}) {
  const t = useTranslations('campaigns');
  const locale = useLocale();
  const days = Math.max(0, ...campaign.steps.map((s) => s.sendAfterDays));
  const from = pickedLabels(senders);
  const live = campaign.status === 'active' || campaign.status === 'paused';
  return (
    <CampaignHead
      title={<h1 className="text-title">{campaign.name}</h1>}
      state={<StatusDot tone={TONE[campaign.status]}>{t(`status.${campaign.status}`)}</StatusDot>}
      meta={
        <p className="flex flex-wrap items-center gap-x-1">
          <span>
            {from.length > 0
              ? t('detail.from', { senders: from.map((s) => s.label).join(', ') })
              : t('detail.noSender')}
          </span>
          {live && <SendersDialog campaign={campaign} senders={senders} />}
          <span aria-hidden="true">·</span>
          <span>
            {t('detail.window', {
              days: sendDays(campaign.sendWindowDays, locale),
              start: campaign.sendWindowStartHour,
              end: campaign.sendWindowEndHour,
            })}
          </span>
          <span aria-hidden="true">·</span>
          <span>{t('detail.length', { steps: campaign.steps.length, days })}</span>
        </p>
      }
      actions={campaign.steps.length > 0 ? <StepsDialog steps={campaign.steps} /> : undefined}
    />
  );
}

export function CampaignTiles({ campaign: c }: { campaign: Campaign }) {
  const t = useTranslations('campaigns.detail');
  const format = useFormatter();
  const n = (value: number) => format.number(value, 'credits');
  return (
    <KpiStrip
      stats={[
        { label: t('people'), value: n(c.targetCount) },
        {
          label: t('sent'),
          value: n(c.sent),
          note: t('sentToday', { sent: c.sentToday, cap: c.dailyCap }),
        },
        {
          label: t('repliesTile'),
          value: n(c.replied),
          note: t('repliesDetail', { interested: c.interested, auto: c.autoReplies }),
        },
        {
          label: t('failed'),
          value: n(c.failed),
          note: c.invalidEmail > 0 ? t('invalidEmail', { count: c.invalidEmail }) : undefined,
        },
      ]}
    />
  );
}

/** The right column: the sequence and who it sends from. Phone keeps one column, so it is hidden there. */
export function CampaignSide({
  campaign,
  senders,
}: {
  campaign: Campaign;
  senders: CampaignSenders | null;
}) {
  const t = useTranslations('campaigns.detail');
  const ts = useTranslations('campaigns.step.channels');
  const tb = useTranslations('campaignBuilder.steps');
  const days = Math.max(0, ...campaign.steps.map((s) => s.sendAfterDays));
  const from = pickedLabels(senders);
  return (
    <div className="flex flex-col gap-4 phone:hidden">
      <Panel title={t('sequence')} meta={t('length', { steps: campaign.steps.length, days })} flush>
        {campaign.steps.map((s, i) => (
          <div
            key={i}
            className="border-hair flex items-center gap-2.5 border-b px-3 py-2.5 last:border-b-0"
          >
            <span className="text-data-sm text-muted w-5 font-mono">
              {t('dayShort', { day: s.sendAfterDays })}
            </span>
            <StepIcon channel={s.channel} />
            <span className="text-ui-strong text-ink">
              {tb('heading', { number: i + 1, channel: ts(s.channel) })}
            </span>
          </div>
        ))}
      </Panel>
      <Panel title={t('sendersTitle')} flush>
        {from.length === 0 ? (
          <p className="text-ui text-muted px-3 py-2.5">{t('noSender')}</p>
        ) : (
          from.map(({ id, label }) => (
            <div
              key={id}
              className="border-hair text-ui-strong text-ink flex items-center gap-2.5 border-b px-3 py-2.5 last:border-b-0"
            >
              <Mail className="text-soft size-3.75" aria-hidden />
              <span className="min-w-0 truncate">{label}</span>
            </div>
          ))
        )}
      </Panel>
    </div>
  );
}
