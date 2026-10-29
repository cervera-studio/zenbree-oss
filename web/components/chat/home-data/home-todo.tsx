import { ArrowRight, Check } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { rowFocus } from '@/components/app/nav';
import { Badge, TINT, type Tone } from '@/components/ui/badge';
import { approvalsBreakdown } from '@/lib/chat/home-overview';
import type { HomeLive } from '@/lib/home/live';
import type { HomeOverview } from '@/lib/home/overview';
import { cn } from '@/lib/utils';
import { HomePanel, ROW } from './home-panel';
import { Glow, popClass } from './home-pulse';
import { useWhen } from './use-when';

const ROW_LINK = cn(ROW, 'hover:bg-raised relative transition-colors', rowFocus);

function GoLabel({ children }: { children: string }) {
  return (
    <span className="text-accent-ink text-small inline-flex items-center gap-1 font-bold whitespace-nowrap phone:hidden">
      {children}
      <ArrowRight aria-hidden className="size-3.5" />
    </span>
  );
}

function Count({ n, pulse, tone }: { n: number; pulse?: number; tone: Tone }) {
  const format = useFormatter();
  return (
    <span
      key={pulse}
      className={cn(
        'text-data-sm inline-flex h-5.5 min-w-7 shrink-0 items-center justify-center rounded-sm px-1.5 font-mono font-bold tabular-nums',
        TINT[tone],
        popClass(pulse),
      )}
    >
      {format.number(n, 'credits')}
    </span>
  );
}

function ApprovalsRow({
  approvals,
  pulse,
}: {
  approvals: HomeOverview['approvals'];
  pulse?: number;
}) {
  const t = useTranslations('chat.home.data.todo');
  const th = useTranslations('chat.home.todo');
  const { campaigns, other } = approvalsBreakdown(approvals);
  const parts = [
    ...campaigns.map((c) => t('inCampaign', { count: c.count, name: c.name })),
    ...(other > 0 ? [t('inOthers', { count: other })] : []),
  ];
  return (
    <Link href={campaigns[0] ? `/campaigns/${campaigns[0].id}` : '/campaigns'} className={ROW_LINK}>
      <Glow pulse={pulse} className="rounded-none" />
      <Count n={approvals.total} pulse={pulse} tone="warning" />
      <span className="flex min-w-0 flex-1 flex-col py-1.5">
        <strong className="text-ui-strong">{th('approvals')}</strong>
        <span className="text-small text-muted">{parts.join(', ')}</span>
      </span>
      <GoLabel>{t('review')}</GoLabel>
    </Link>
  );
}

function RepliesRow({ replies, pulse }: { replies: HomeOverview['replies']; pulse?: number }) {
  const t = useTranslations('chat.home.data.todo');
  const ti = useTranslations('inbox.label');
  const when = useWhen();
  const { latest } = replies;
  const tag = latest?.intent ?? null;
  const parts = latest
    ? [latest.name, latest.campaign, when(latest.at, { clock: true })].filter(Boolean)
    : [];
  return (
    <Link href="/inbox" className={ROW_LINK}>
      <Glow pulse={pulse} className="rounded-none" />
      <Count n={replies.count} pulse={pulse} tone="accent" />
      <span className="flex min-w-0 flex-1 flex-col py-1.5">
        <strong className="text-ui-strong">{t('repliesTitle')}</strong>
        {latest && (
          <span className="text-small text-muted">
            {t('latest', { parts: parts.join(' · ') })}
            {tag && (
              <span className="ml-1.5 inline-flex align-middle">
                <Badge tone="positive">{ti(tag)}</Badge>
              </span>
            )}
          </span>
        )}
      </span>
      <GoLabel>{t('read')}</GoLabel>
    </Link>
  );
}

export function HomeTodo({
  overview,
  pulses,
}: {
  overview: HomeOverview;
  pulses: HomeLive['pulses'];
}) {
  const t = useTranslations('chat.home.todo');
  const td = useTranslations('chat.home.data.todo');
  const { approvals, replies } = overview;
  return (
    <HomePanel title={t('title')}>
      {approvals.total === 0 && replies.count === 0 ? (
        <p className="text-ui text-muted flex items-center gap-2.5 px-3 py-3">
          <Check aria-hidden className="text-positive size-4 shrink-0" />
          {td('quiet')}
        </p>
      ) : (
        <>
          {approvals.total > 0 && <ApprovalsRow approvals={approvals} pulse={pulses.approvals} />}
          {replies.count > 0 && <RepliesRow replies={replies} pulse={pulses.replies} />}
        </>
      )}
    </HomePanel>
  );
}
