'use client';

import { TriangleAlert } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { RuleProblem } from '@/lib/segments/api';
import type { FieldCatalog } from '@/lib/segments/fields';
import { nodeOf } from '@/lib/segments/rule';

const position = (path: string) =>
  nodeOf(path)
    .split('/children/')
    .slice(1)
    .map((i) => Number(i) + 1)
    .join('.');

export function FrozenBanner({
  problems,
  catalog,
}: {
  problems: RuleProblem[];
  catalog: FieldCatalog;
}) {
  const t = useTranslations('segments.frozen');
  if (problems.length === 0) return null;
  return (
    <section
      aria-labelledby="segment-frozen"
      className="bg-warning-tint text-ink flex gap-3 rounded-md p-3"
    >
      <TriangleAlert aria-hidden className="text-warning mt-0.5 size-4 shrink-0" />
      <div className="flex min-w-0 flex-col gap-1.5">
        <h2 id="segment-frozen" className="text-ui-strong">
          {t('title')}
        </h2>
        <p className="text-ui">{t('effect')}</p>
        <ul className="text-ui flex list-disc flex-col gap-1 pl-4.5">
          {problems.map((p) => {
            const list = catalog.lists.find((l) => l.id === p.list_id)?.name;
            const where = position(p.path);
            return (
              <li key={p.path} className="break-words">
                {p.reason === 'list_not_found'
                  ? t('listGone', { where })
                  : t('columnGone', { where, column: p.key ?? '', list: list ?? '' })}
              </li>
            );
          })}
        </ul>
        <p className="text-ui">{t('fix')}</p>
      </div>
    </section>
  );
}
