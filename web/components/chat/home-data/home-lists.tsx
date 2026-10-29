import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Badge, Tag } from '@/components/ui/badge';
import { initials } from '@/lib/chat/home-overview';
import type { HomeOverview } from '@/lib/home/overview';
import { cn } from '@/lib/utils';
import { GoLink, HomePanel, ROW } from './home-panel';
import { useWhen } from './use-when';

type List = HomeOverview['lists'][number];

const SHOWN_ON_PHONE = 3;

function Initials({ name }: { name: string | null }) {
  const t = useTranslations('chat.home.data.lists');
  return (
    <span
      title={name ?? t('formerMember')}
      className="bg-accent-tint text-accent-ink inline-flex size-5.5 shrink-0 items-center justify-center rounded-sm text-3xs font-bold"
    >
      {name ? initials(name) : '?'}
    </span>
  );
}

function kindOf(list: List): 'enrich' | List['entity'] {
  return list.mode === 'enrich' ? 'enrich' : list.entity;
}

function ListRow({ list, index, fresh }: { list: List; index: number; fresh: boolean }) {
  const t = useTranslations('chat.home.data.lists');
  const when = useWhen();
  return (
    <li
      className={cn(
        ROW,
        'flex-wrap py-1.5',
        index >= SHOWN_ON_PHONE && 'phone:hidden',
        fresh && 'animate-home-row-glow',
      )}
    >
      <Initials name={list.owner?.name ?? null} />
      <div className="flex min-w-40 flex-1 flex-col">
        <span className={cn('flex min-w-0 items-center gap-1.5', fresh && 'animate-home-arrive')}>
          <Link href={`/lists/${list.id}`} className="text-ui-strong truncate hover:underline">
            {list.name}
          </Link>
          {fresh && <Badge tone="accent">{t('fresh')}</Badge>}
        </span>
        <span className="text-small text-muted">
          {t(`kind.${kindOf(list)}`)}
          <span className="phone:hidden">{` · ${t('rowCount', { count: list.rows })}`}</span>
        </span>
      </div>
      {list.verifiedEmails === null ? (
        <span className="text-data text-muted font-mono">—</span>
      ) : (
        <Tag tone="positive">{t('verifiedCount', { count: list.verifiedEmails })}</Tag>
      )}
      <span className="text-small text-muted w-28 text-right phone:hidden">
        {when(list.updatedAt)}
      </span>
    </li>
  );
}

export function HomeLists({ lists, fresh }: { lists: List[]; fresh: readonly string[] }) {
  const t = useTranslations('chat.home.data.lists');
  return (
    <HomePanel title={t('title')} action={<GoLink href="/lists">{t('all')}</GoLink>}>
      {lists.length === 0 ? (
        <p className="text-ui text-muted px-3 py-3">{t('empty')}</p>
      ) : (
        <ul className="flex flex-col">
          {lists.map((list, i) => (
            <ListRow key={list.id} list={list} index={i} fresh={fresh.includes(list.id)} />
          ))}
        </ul>
      )}
    </HomePanel>
  );
}
