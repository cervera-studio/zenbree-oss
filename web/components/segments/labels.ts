'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useCallback } from 'react';
import type { FieldCatalog } from '@/lib/segments/fields';
import type { Condition, Pick, RuleField } from '@/lib/segments/rule';

type ImportField = Parameters<ReturnType<typeof useTranslations<'lists.importFields'>>>[0];

function useFieldLabel(catalog: FieldCatalog) {
  const tf = useTranslations('lists.importFields');
  const t = useTranslations('segments.fields');
  return useCallback(
    (field: RuleField) => {
      if (field.kind === 'canonical') {
        return tf.has(field.key as ImportField) ? tf(field.key as ImportField) : field.key;
      }
      const list = catalog.lists.find((l) => l.id === field.list_id);
      const column = catalog.columns[field.list_id]?.find((c) => c.key === field.key);
      return t('column', { column: column?.label ?? field.key, list: list?.name ?? t('goneList') });
    },
    [catalog, t, tf],
  );
}

export function usePickLabel(catalog: FieldCatalog) {
  const t = useTranslations('segments.fields');
  const fieldLabel = useFieldLabel(catalog);
  return useCallback(
    (pick: Pick) => {
      if (pick.type === 'fit') return t('fit');
      if (pick.type === 'in_list') {
        const list = catalog.lists.find((l) => l.id === pick.list_id);
        return t('inList', { list: list?.name ?? t('goneList') });
      }
      return fieldLabel(pick.field);
    },
    [catalog, fieldLabel, t],
  );
}

export function useConditionText(catalog: FieldCatalog) {
  const t = useTranslations('segments');
  const format = useFormatter();
  const fieldLabel = useFieldLabel(catalog);
  return useCallback(
    (c: Condition) => {
      if (c.type === 'in_list') {
        const list = catalog.lists.find((l) => l.id === c.list_id)?.name ?? t('fields.goneList');
        return t(`inList.${c.op}`, { list });
      }
      const subject = c.type === 'fit' ? t('fields.fit') : fieldLabel(c.field);
      const n = (v: number | null | undefined) =>
        typeof v === 'number' ? format.number(v, 'credits') : '';
      const detail =
        c.type === 'text'
          ? c.values.join(', ')
          : c.type === 'number' || c.type === 'fit'
            ? [n(c.low), n(c.high)].filter(Boolean).join(` ${t('ops.boundsJoin')} `)
            : '';
      return [subject, t(`ops.${c.op}`), detail].filter(Boolean).join(' ');
    },
    [catalog, fieldLabel, format, t],
  );
}
