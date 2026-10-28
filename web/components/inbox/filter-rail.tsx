import { X } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { DOT } from '@/components/ui/badge';
import { LABELS, type Label } from '@/lib/inbox/api';
import { type Clean, href } from '@/lib/inbox/filters';
import { cn } from '@/lib/utils';
import { ChannelSelect } from './channel-select';
import { TONE } from './state-tag';

type View = { key: string; label?: string; unread?: string; kind?: string; dot: string };

const viewOf = (f: Clean) => (f.unread ? 'unread' : f.kind ? 'unsubscribe' : (f.label ?? 'all'));

// The design lists `Non ora` before `Non interessato`. A missing label fails the type.
const RANK: Record<Label, number> = {
  interested: 0,
  meeting_request: 1,
  not_now: 2,
  not_interested: 3,
  other: 4,
};
const ORDER = [...LABELS].sort((a, b) => RANK[a] - RANK[b]);

const ITEM =
  'flex items-center gap-2 rounded-md border border-transparent whitespace-nowrap hover:bg-hair desktop:h-control desktop:px-2 phone:min-h-11 phone:shrink-0 phone:rounded-full phone:border-line phone:px-3.5';
const ON =
  'border-line bg-ground text-ink hover:bg-ground phone:border-accent/40 phone:bg-accent-tint phone:text-accent-ink';

/** The views of the inbox with their counts, and the channel select (chips on a phone). */
export async function FilterRail({
  filters,
  counts,
  campaign,
}: {
  filters: Clean;
  counts: Record<string, number>;
  campaign: string | null;
}) {
  const t = await getTranslations('inbox');
  const views: View[] = [
    { key: 'all', dot: 'bg-ink' },
    { key: 'unread', unread: '1', dot: 'bg-accent' },
    ...[...ORDER, 'none' as const].map((l) => ({
      key: l,
      label: l,
      dot: l === 'meeting_request' ? 'bg-accent-ink' : DOT[TONE[l]],
    })),
    { key: 'unsubscribe', kind: 'unsubscribe', dot: DOT.negative },
  ];
  const current = viewOf(filters);
  // The client camel-cases the payload: `meeting_request` arrives as `meetingRequest`.
  const count = (key: string) => counts[key.replace(/_(\w)/g, (_, c: string) => c.toUpperCase())];
  const channels = [undefined, 'email', 'linkedin'] as const;
  return (
    <div className="flex flex-col gap-0.5 desktop:bg-raised desktop:border-line desktop:w-53 desktop:shrink-0 desktop:overflow-y-auto desktop:border-r desktop:px-3 desktop:py-4 phone:gap-4 phone:px-4 phone:pt-4 phone:pb-3">
      <h1 className="text-title desktop:px-2 desktop:pb-2.5">{t('title')}</h1>
      {filters.campaign && (
        <Link
          href={href({ ...filters, campaign: undefined, offset: 0 })}
          aria-label={t('removeCampaign')}
          className="bg-accent-tint text-accent-ink text-ui-strong flex min-h-control w-fit max-w-full items-center gap-2 rounded-full px-3 desktop:mx-2 desktop:mb-1.5 phone:min-h-11"
        >
          <span className="truncate">{t('campaignChip', { name: campaign ?? t('campaign') })}</span>
          <X aria-hidden="true" className="size-3.5 shrink-0" />
        </Link>
      )}
      <nav
        aria-label={t('filters')}
        className="flex flex-col gap-0.5 phone:-mx-4 phone:flex-row phone:gap-2 phone:overflow-x-auto phone:px-4 phone:scrollbar-none"
      >
        {views.map(({ key, dot, ...params }) => {
          const on = current === key;
          const n = count(key);
          return (
            <Link
              key={key}
              href={href({
                ...params,
                channel: filters.channel,
                campaign: filters.campaign,
                sort: filters.sort,
              })}
              aria-current={on ? 'page' : undefined}
              className={cn(ITEM, on && ON)}
            >
              <span
                aria-hidden="true"
                className={cn('size-1.75 shrink-0 rounded-full phone:hidden', dot)}
              />
              <span className={cn('flex-1', on ? 'text-ui-strong' : 'text-ui text-soft')}>
                {t(`view.${key}` as 'view.all')}
              </span>
              {Boolean(n) && (
                <span className="text-data-sm text-muted font-mono tabular-nums">{n}</span>
              )}
            </Link>
          );
        })}
        {channels.map((ch) => (
          <Link
            key={ch ?? 'any'}
            href={href({ ...filters, offset: 0, channel: ch })}
            aria-current={filters.channel === ch ? 'page' : undefined}
            className={cn(ITEM, 'text-ui desktop:hidden', filters.channel === ch && ON)}
          >
            {ch ? t(`channel.${ch}`) : t('anyChannel')}
          </Link>
        ))}
      </nav>
      <div className="pt-4 phone:hidden">
        <ChannelSelect
          label={t('channelLabel')}
          value={filters.channel ?? ''}
          options={channels.map((ch) => ({
            value: ch ?? '',
            text: ch ? t(`channel.${ch}`) : t('anyChannel'),
            href: href({ ...filters, offset: 0, channel: ch }),
          }))}
        />
      </div>
    </div>
  );
}
