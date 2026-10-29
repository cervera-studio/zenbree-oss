'use client';

import { useTranslations } from 'next-intl';
import type { ComponentProps } from 'react';
import { Select } from '@/components/ui/select';
import { canonicalPicks, columnPicks, encodePick, type FieldCatalog } from '@/lib/segments/fields';
import type { Pick } from '@/lib/segments/rule';
import { usePickLabel } from './labels';

export function FieldPicker({
  catalog,
  value,
  placeholder,
  onPick,
  ...props
}: {
  catalog: FieldCatalog;
  value: string;
  placeholder: string;
  onPick: (pick: Pick) => void;
} & Omit<ComponentProps<typeof Select>, 'value' | 'onChange' | 'children'>) {
  const t = useTranslations('segments.fields');
  const label = usePickLabel(catalog);
  const options = new Map<string, Pick>();
  const group = (picks: Pick[]) =>
    picks.map((pick) => {
      const key = encodePick(pick);
      options.set(key, pick);
      return (
        <option key={key} value={key}>
          {label(pick)}
        </option>
      );
    });
  const withColumns = catalog.lists.filter((l) => catalog.columns[l.id]);
  const body = (
    <>
      <optgroup label={t('canonical')}>{group(canonicalPicks(catalog.entity))}</optgroup>
      {withColumns.map((l) => (
        <optgroup key={l.id} label={t('columnsOf', { list: l.name })}>
          {group(columnPicks(l.id, catalog.columns[l.id] ?? []))}
        </optgroup>
      ))}
      <optgroup label={t('score')}>{group([{ type: 'fit' }])}</optgroup>
      {catalog.lists.length > 0 && (
        <optgroup label={t('membership')}>
          {group(catalog.lists.map((l) => ({ type: 'in_list', list_id: l.id })))}
        </optgroup>
      )}
    </>
  );
  return (
    <Select
      touch
      value={value}
      onChange={(e) => {
        const pick = options.get(e.target.value);
        if (pick) onPick(pick);
      }}
      {...props}
    >
      {value === '' && <option value="">{placeholder}</option>}
      {value !== '' && !options.has(value) && <option value={value}>{t('unavailable')}</option>}
      {body}
    </Select>
  );
}
