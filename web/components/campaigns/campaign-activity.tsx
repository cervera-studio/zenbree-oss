'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';
import { loadActivityAction } from '@/app/(app)/campaigns/actions';
import { DOT, type Tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/ui/panel';
import { useBrowserTimeZone } from '@/i18n/browser-time-zone';
import { eventKey, mergeActivity, panelActivity } from '@/lib/campaigns/activity';
import type { Activity, ActivityEvent } from '@/lib/campaigns/api';
import { dayRelation } from '@/lib/chat/home-overview';
import { useAction } from '@/lib/hooks/use-action';
import { cn } from '@/lib/utils';

const TONE: Record<ActivityEvent['kind'], Tone> = {
  sent: 'neutral',
  approved: 'accent',
  reply: 'positive',
  auto_reply: 'warning',
  unsubscribe: 'negative',
};

type Older = { items: ActivityEvent[]; cursor: string | null };

/** Attività recente (#864): the server's first page, and the older ones «Carica altri» brings in. */
export function CampaignActivity({
  campaignId,
  first,
}: {
  campaignId: string;
  // null: the first page did not load.
  first: Activity | null;
}) {
  const t = useTranslations('campaigns.activity');
  const { pending, error, run } = useAction();
  const [older, setOlder] = useState<Older | null>(null);
  const events = panelActivity(first, older?.items ?? null);
  const cursor = older ? older.cursor : (first?.nextCursor ?? null);

  const loadMore = () => {
    if (!cursor) return;
    // From the events on screen, so a first page kept out of the panel cannot join later over a gap.
    void run(() => loadActivityAction(campaignId, cursor), {
      onSuccess: (page) =>
        setOlder({ items: mergeActivity(events, page.items), cursor: page.nextCursor }),
    });
  };

  return (
    <Panel title={t('title')} flush>
      {events.length > 0 ? (
        <ul>
          {events.map((e) => (
            <ActivityRow key={eventKey(e)} event={e} />
          ))}
        </ul>
      ) : (
        first && <p className="text-ui text-muted px-3 py-2.5">{t('empty')}</p>
      )}
      {/* A failed refresh of the first page keeps the pages loaded before it on screen. */}
      {(!first || error) && (
        <p role="alert" className="text-negative text-small px-3 py-2.5">
          {t('loadError')}
        </p>
      )}
      {cursor && (
        <div className="px-3 py-2">
          <Button variant="link" size="sm" disabled={pending} onClick={loadMore}>
            {t('loadMore')}
          </Button>
        </div>
      )}
    </Panel>
  );
}

function ActivityRow({ event: e }: { event: ActivityEvent }) {
  const t = useTranslations('campaigns.activity');
  const name = [e.firstName, e.lastName].filter(Boolean).join(' ') || e.companyName;
  return (
    <li className="border-hair flex items-center gap-2.5 border-b px-3 py-2.5 last:border-b-0">
      <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', DOT[TONE[e.kind]])} />
      <span className="flex min-w-0 grow flex-col">
        <span className={cn('text-ui-strong truncate', name ? 'text-ink' : 'text-muted')}>
          {name || t('deleted')}
        </span>
        <span className="text-small text-muted">
          <EventLine event={e} />
        </span>
      </span>
      <EventTime at={e.at} />
    </li>
  );
}

function EventLine({ event: e }: { event: ActivityEvent }) {
  const t = useTranslations('campaigns.activity');
  const tc = useTranslations('campaigns.step.channels');
  const tl = useTranslations('inbox.label');
  const ti = useTranslations('inbox.channel');
  const step = e.step ?? 0;
  switch (e.kind) {
    case 'sent':
      // `linkedin` is the inbound channel: a send names its step's channel.
      return t('sent', {
        step,
        channel: e.channel === 'linkedin' ? ti('linkedin') : tc(e.channel),
      });
    case 'approved':
      return t('approved', { step });
    case 'reply':
      return e.label ? t('reply', { label: tl(e.label) }) : t('replied');
    case 'auto_reply':
      return t('auto_reply');
    case 'unsubscribe':
      return t('unsubscribe');
  }
}

/** Today a clock, yesterday a word, else a short date, in the user's zone once it is known. */
function EventTime({ at }: { at: string }) {
  const t = useTranslations('campaigns.activity');
  const format = useFormatter();
  const timeZone = useBrowserTimeZone();
  if (!timeZone) return null;
  const when = new Date(at);
  const day = dayRelation(when, new Date(), timeZone);
  const text =
    day === 'today'
      ? format.dateTime(when, { hour: '2-digit', minute: '2-digit', timeZone })
      : day === 'yesterday'
        ? t('yesterday')
        : format.dateTime(when, { day: 'numeric', month: 'short', timeZone });
  return (
    <time dateTime={at} className="text-data-sm text-muted shrink-0 font-mono tabular-nums">
      {text}
    </time>
  );
}
