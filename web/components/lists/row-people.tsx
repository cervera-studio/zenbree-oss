import Link from 'next/link';
import { useTranslations } from 'next-intl';
import type { ListRow } from '@/lib/lists/api';
import { RowName } from './row-cells';

/** A company row's people from the chat's Persone list, each linking to the person's row (#344). */
export function RowPeople({ row, chatHref }: { row: ListRow; chatHref: string | null }) {
  const t = useTranslations('lists');
  const people = row.people ?? [];
  if (people.length === 0) {
    return (
      <span className="text-muted text-xs">
        {t('noDecisionMaker')}
        {chatHref && (
          <>
            {' · '}
            <Link href={chatHref} className="text-accent-ink hover:underline">
              {t('askInChat')}
            </Link>
          </>
        )}
      </span>
    );
  }
  const more = (row.people_count ?? 0) - people.length;
  return (
    <ul className="flex flex-col gap-1">
      {people.map((p) => (
        <li key={p.row_id} className="flex flex-col">
          <Link
            href={`/lists/${p.list_id}?row=${p.row_id}`}
            className="group hover:text-accent-ink text-xs-plus font-semibold hover:underline"
          >
            <RowName name={p.full_name ?? ''} />
          </Link>
          {p.title && <span className="text-muted text-xs">{p.title}</span>}
        </li>
      ))}
      {more > 0 && <li className="text-muted text-xs">{t('morePeople', { count: more })}</li>}
    </ul>
  );
}
