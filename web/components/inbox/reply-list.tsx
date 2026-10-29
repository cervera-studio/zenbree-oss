import { ArrowDownUp } from 'lucide-react';
import Link from 'next/link';
import { getFormatter, getTranslations } from 'next-intl/server';
import { Avatar } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { type Clean, href } from '@/lib/inbox/filters';
import { cn } from '@/lib/utils';
import { StateTag } from './state-tag';
import { UnverifiedMarker } from './thread-notices';
import { type Message, personName } from './types';

async function ReplyItem({
  message: m,
  filters,
  selected,
}: {
  message: Message;
  filters: Clean;
  selected: boolean;
}) {
  const [t, format] = await Promise.all([getTranslations('inbox'), getFormatter()]);
  const name = personName(m);
  return (
    <li className="border-hair border-b">
      <Link
        href={href({ ...filters, m: m.id })}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'flex items-start gap-2.5 px-4 py-2.75 phone:py-3.5',
          selected ? 'bg-selected' : 'hover:bg-hair',
        )}
      >
        <Avatar user={{ name, email: m.fromAddress }} tone={selected ? 'accent' : 'neutral'} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.75">
          <span className="flex items-center gap-1.5">
            {!m.readAt && (
              <span aria-hidden="true" className="size-1.75 shrink-0 rounded-full bg-accent" />
            )}
            <span className="text-ui-strong min-w-0 flex-1 truncate">{name}</span>
            {!m.readAt && <span className="sr-only">{t('unreadSr')}</span>}
            <span className="text-small text-muted shrink-0">
              {format.relativeTime(new Date(m.receivedAt), new Date())}
            </span>
          </span>
          <span className="text-small text-muted truncate">
            {[m.targetCompanyName, t(`channel.${m.channel}`)].filter(Boolean).join(' · ')}
          </span>
          <span className="text-ui text-soft truncate">{m.body || m.subject}</span>
          <span className="flex gap-1.5 empty:hidden">
            <StateTag message={m} />
            <UnverifiedMarker authenticated={m.senderAuthenticated} />
          </span>
        </span>
      </Link>
    </li>
  );
}

/** The conversation list, newest or oldest first, with the one on show highlighted. */
export async function ReplyList({
  messages,
  filters,
  shownId,
  loadFailed,
  full,
}: {
  messages: Message[];
  filters: Clean;
  shownId: string | undefined;
  loadFailed: boolean;
  full: boolean;
}) {
  const t = await getTranslations('inbox');
  const oldest = filters.sort === 'oldest';
  // With more pages the length is only this page's, and a failed load has no count: say nothing then.
  const head =
    !loadFailed && filters.offset === 0 && !full
      ? t(oldest ? 'listCountOldest' : 'listCount', { n: messages.length })
      : t(oldest ? 'oldestFirst' : 'newestFirst');
  return (
    <>
      <div className="border-line flex h-11 shrink-0 items-center gap-2 border-b pr-1 pl-4 phone:h-12">
        <p className="text-small text-muted min-w-0 flex-1 truncate">{head}</p>
        <Button asChild variant="ghost" size="icon-md" className="phone:size-11">
          <Link
            href={href({ ...filters, offset: 0, sort: oldest ? undefined : 'oldest' })}
            aria-label={t(oldest ? 'sortNewest' : 'sortOldest')}
            title={t(oldest ? 'sortNewest' : 'sortOldest')}
          >
            <ArrowDownUp aria-hidden="true" />
          </Link>
        </Button>
      </div>
      {loadFailed && <p className="text-negative text-ui p-4">{t('loadError')}</p>}
      {!loadFailed && messages.length === 0 && (
        <p className="text-muted text-ui p-4">{t('emptyFiltered')}</p>
      )}
      <ul className="min-h-0 flex-1 overflow-y-auto">
        {messages.map((m) => (
          <ReplyItem key={m.id} message={m} filters={filters} selected={m.id === shownId} />
        ))}
      </ul>
    </>
  );
}
