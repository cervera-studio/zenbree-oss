import { useFormatter, useTranslations } from 'next-intl';
import type { MemberRow, Members } from '@/lib/segments/api';
import type { FieldCatalog } from '@/lib/segments/fields';
import { RowName } from './row-name';
import { WhyButton } from './why-drawer';

const SHOWN_CELLS = 6;

type ImportField = Parameters<ReturnType<typeof useTranslations<'lists.importFields'>>>[0];

function Cells({ row, catalog }: { row: MemberRow; catalog: FieldCatalog }) {
  const tf = useTranslations('lists.importFields');
  const columns = catalog.columns[row.list_id] ?? [];
  const label = (key: string) =>
    columns.find((c) => c.key === key)?.label ??
    (tf.has(key as ImportField) ? tf(key as ImportField) : key);
  const filled = Object.entries(row.cells)
    .filter(([, v]) => (typeof v === 'string' && v.trim() !== '') || typeof v === 'number')
    .slice(0, SHOWN_CELLS);
  if (filled.length === 0) return null;
  return (
    <dl className="text-small flex flex-wrap gap-x-4 gap-y-0.5">
      {filled.map(([key, value]) => (
        <div key={key} className="flex min-w-0 gap-1">
          <dt className="text-soft">{label(key)}</dt>
          <dd className="text-ink min-w-0 break-words">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function MembersList({
  segmentId,
  members,
  catalog,
}: {
  segmentId: string;
  members: Members['members'];
  catalog: FieldCatalog;
}) {
  const t = useTranslations('segments.members');
  const format = useFormatter();
  if (members.length === 0) return <p className="text-ink text-ui">{t('none')}</p>;
  return (
    <ul className="border-line bg-ground flex flex-col rounded-lg border">
      {members.map((m) => {
        const list = m.row && catalog.lists.find((l) => l.id === m.row?.list_id)?.name;
        return (
          <li
            key={m.identity_id}
            className="border-line flex flex-wrap items-start justify-between gap-3 border-b p-3 last:border-b-0"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <RowName row={m.row} />
              <p className="text-soft text-small">
                {t('entered', {
                  when: format.dateTime(new Date(m.entered_at), {
                    dateStyle: 'medium',
                    timeStyle: 'short',
                  }),
                })}
                {m.episode > 1 && ` · ${t('episode', { n: m.episode })}`}
                {list && ` · ${t('fromList', { list })}`}
              </p>
              {m.row && <Cells row={m.row} catalog={catalog} />}
            </div>
            <WhyButton segmentId={segmentId} identityId={m.identity_id} catalog={catalog} />
          </li>
        );
      })}
    </ul>
  );
}
