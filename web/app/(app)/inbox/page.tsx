import { getTranslations } from 'next-intl/server';
import { z } from 'zod';
import { Conversation } from '@/components/inbox/conversation';
import { EmptyInbox } from '@/components/inbox/empty-inbox';
import { FilterRail } from '@/components/inbox/filter-rail';
import { InboxFrame } from '@/components/inbox/frame';
import { Pager } from '@/components/inbox/pager';
import { ReplyList } from '@/components/inbox/reply-list';
import { getCampaign } from '@/lib/campaigns/api';
import { getInbox, getThread, PAGE_SIZE } from '@/lib/inbox/api';
import { clean, type Filters, href } from '@/lib/inbox/filters';
import { cn } from '@/lib/utils';

export default async function InboxPage({ searchParams }: { searchParams: Promise<Filters> }) {
  const sp = await searchParams;
  const filters = clean(sp);
  const t = await getTranslations('inbox');
  // Phone (#171): `?m=` opens one thread full screen. Read before the list, so the list shows it read.
  const opened = z.uuid().safeParse(sp.m).success ? await getThread(sp.m ?? '', true) : null;
  const { campaign, ...rest } = filters;
  const [inbox, campaignFilter] = await Promise.all([
    getInbox({ ...rest, campaign_id: campaign, unread: filters.unread ? true : undefined }),
    campaign ? getCampaign(campaign) : null,
  ]);
  const messages = inbox.success ? inbox.data.messages : [];
  const filtered = Boolean(
    filters.label ||
      filters.channel ||
      filters.unread ||
      filters.kind ||
      campaign ||
      filters.offset,
  );
  const open = opened?.success ? opened.data : null;
  if (inbox.success && messages.length === 0 && !filtered && !open) return <EmptyInbox />;

  // Desktop always shows a thread: the first one, without marking it read.
  const first = messages[0];
  const peek = !open && first ? await getThread(first.id, false) : null;
  const shown = open ?? (peek?.success ? peek.data : null);
  const full = messages.length === PAGE_SIZE;

  return (
    <InboxFrame flush>
      <div
        className={cn(
          'flex min-h-0 shrink-0 desktop:contents',
          open ? 'phone:hidden' : 'phone:flex phone:min-h-0 phone:flex-1 phone:flex-col',
        )}
      >
        <FilterRail
          filters={filters}
          counts={inbox.success ? inbox.data.counts : {}}
          campaign={campaignFilter?.success ? campaignFilter.data.name : null}
        />
        <section
          aria-label={t('threads')}
          className="border-line flex min-h-0 flex-col desktop:w-95 desktop:shrink-0 desktop:border-r phone:flex-1"
        >
          <ReplyList
            messages={messages}
            filters={filters}
            shownId={shown?.message.id}
            loadFailed={!inbox.success}
            full={full}
          />
          <Pager filters={filters} full={full} />
        </section>
      </div>
      {shown && (
        <Conversation
          thread={shown}
          back={href(filters)}
          className={open ? undefined : 'phone:hidden'}
        />
      )}
    </InboxFrame>
  );
}
