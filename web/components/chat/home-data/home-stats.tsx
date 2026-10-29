'use client';

import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { TEXT, type Tone } from '@/components/ui/badge';
import { KpiStrip } from '@/components/ui/kpi';
import { Panel } from '@/components/ui/panel';
import { useBrowserTimeZone } from '@/i18n/browser-time-zone';
import type { HomeStats } from '@/lib/api/home';
import { cn } from '@/lib/utils';
import { ROW } from './home-panel';
import { useWhen } from './use-when';

type Kpi = HomeStats['kpis']['leads'];

const KPI_KEYS = ['leads', 'contacts', 'messagesSent', 'replies', 'positiveReplies'] as const;
const FUNNEL_KEYS = ['leads', 'contacts', 'contacted', 'replied', 'positive'] as const;

/** Whole percent change, or null when there is no base to compare with. */
export function deltaPercent({ value, previous }: Kpi): number | null {
  return previous > 0 ? Math.round(((value - previous) / previous) * 100) : null;
}

/** Bar height as a share of the tallest, with a floor so a small non-zero value stays visible. */
export function barPercent(value: number, max: number): number {
  return value > 0 && max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
}

function Kpis({ kpis }: { kpis: HomeStats['kpis'] }) {
  const t = useTranslations('chat.home.data.stats');
  const format = useFormatter();
  return (
    <div className="flex flex-col gap-1.5">
      <KpiStrip
        stats={KPI_KEYS.map((key) => {
          const pct = deltaPercent(kpis[key]);
          const tone: Tone = !pct ? 'neutral' : pct > 0 ? 'positive' : 'negative';
          return {
            label: t(`kpi.${key}`),
            value: format.number(kpis[key].value, 'credits'),
            note:
              pct === null
                ? undefined
                : format.number(pct / 100, { style: 'percent', signDisplay: 'exceptZero' }),
            tone: pct === null ? undefined : tone,
          };
        })}
      />
      <p className="text-data-sm text-muted font-mono">{t('period')}</p>
    </div>
  );
}

function Legend() {
  const t = useTranslations('chat.home.data.stats');
  return (
    <span className="text-data-sm text-muted flex gap-3">
      {(['sent', 'replies'] as const).map((key) => (
        <span key={key} className="inline-flex items-center gap-1.5">
          <i
            aria-hidden
            className={cn('size-2 rounded-xs', key === 'sent' ? 'bg-chart-1' : 'bg-chart-3')}
          />
          {t(`legend.${key}`)}
        </span>
      ))}
    </span>
  );
}

function WeeklyChart({ weeks }: { weeks: HomeStats['weeks'] }) {
  const t = useTranslations('chat.home.data.stats');
  const format = useFormatter();
  const max = Math.max(...weeks.flatMap((w) => [w.messagesSent, w.replies]), 0);
  return (
    <Panel title={t('weeks.title')} meta={t('weeks.meta')} action={<Legend />}>
      <div className="flex h-chart items-end gap-2 phone:gap-1">
        {weeks.map((w, i) => {
          const label = format.dateTime(new Date(w.week), {
            day: 'numeric',
            month: 'short',
            timeZone: 'UTC',
          });
          return (
            <div
              key={w.week}
              role="img"
              aria-label={t('weeks.column', {
                week: label,
                sent: w.messagesSent,
                replies: w.replies,
              })}
              className="flex h-full min-w-0 flex-1 flex-col gap-1.5"
            >
              <div
                aria-hidden
                className="border-line flex min-h-0 flex-1 items-end justify-center gap-0.5 border-b"
              >
                <i
                  className="bg-chart-1 block w-full max-w-5 rounded-t-xs"
                  style={{ height: `${barPercent(w.messagesSent, max)}%` }}
                />
                <i
                  className="bg-chart-3 block w-full max-w-5 rounded-t-xs"
                  style={{ height: `${barPercent(w.replies, max)}%` }}
                />
              </div>
              <span
                aria-hidden
                className={cn(
                  'text-data-sm text-muted flex justify-center font-mono whitespace-nowrap',
                  i % 2 === 1 && 'phone:invisible',
                )}
              >
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function Funnel({ funnel }: { funnel: HomeStats['funnel'] }) {
  const t = useTranslations('chat.home.data.stats');
  const format = useFormatter();
  const max = Math.max(...FUNNEL_KEYS.map((k) => funnel[k]), 0);
  return (
    <Panel title={t('funnel.title')} meta={t('funnel.meta')}>
      <ul className="flex flex-col gap-3">
        {FUNNEL_KEYS.map((key) => (
          <li key={key} className="flex flex-col gap-1">
            <div className="text-data flex items-baseline justify-between gap-2">
              <span className="text-soft">{t(`funnel.${key}`)}</span>
              <span className="font-mono tabular-nums">
                {format.number(funnel[key], 'credits')}
              </span>
            </div>
            <div className="bg-hair h-2 overflow-hidden rounded-xs">
              <i
                className="bg-accent block h-full rounded-xs"
                style={{ width: `${barPercent(funnel[key], max)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ActivityLog({ activity }: { activity: HomeStats['activity'] }) {
  const t = useTranslations('chat.home.data.stats.activity');
  const when = useWhen();
  const timeZone = useBrowserTimeZone();
  return (
    <Panel title={t('title')} flush className="min-w-0 overflow-hidden">
      {activity.length === 0 ? (
        <p className="text-muted text-small px-3 py-4">{t('empty')}</p>
      ) : (
        <ul>
          {activity.map((e) => {
            const title = e.title ?? t('untitled');
            return (
              <li
                key={`${e.kind}-${e.at}-${e.link}`}
                className={cn(ROW, 'phone:flex-wrap phone:gap-y-0 phone:py-2')}
              >
                <span className="text-data-sm text-muted w-28 shrink-0 truncate font-mono phone:w-full">
                  {t(`kind.${e.kind}`)}
                </span>
                {e.link ? (
                  <Link
                    href={e.link}
                    className="text-ui-strong min-w-0 flex-1 truncate hover:underline"
                  >
                    {title}
                  </Link>
                ) : (
                  <span className={cn('min-w-0 flex-1 truncate', TEXT.neutral)}>{title}</span>
                )}
                <time
                  dateTime={e.at}
                  className="text-data text-muted shrink-0 font-mono tabular-nums"
                >
                  {timeZone ? when(e.at) : ''}
                </time>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

export function HomeStatsPanels({ stats }: { stats: HomeStats }) {
  return (
    <>
      <Kpis kpis={stats.kpis} />
      <div className="grid grid-cols-12 items-start gap-4 phone:grid-cols-1">
        <div className="col-span-7 min-w-0 phone:col-span-1">
          <WeeklyChart weeks={stats.weeks} />
        </div>
        <div className="col-span-5 min-w-0 phone:col-span-1">
          <Funnel funnel={stats.funnel} />
        </div>
      </div>
      <ActivityLog activity={stats.activity} />
    </>
  );
}
