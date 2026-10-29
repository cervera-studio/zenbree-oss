'use client';

import { Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { relabelAction } from '@/app/(app)/inbox/actions';
import { Select } from '@/components/ui/select';
import { useAction } from '@/lib/hooks/use-action';

export function LabelSelect({
  id,
  label,
  labels,
  auto,
}: {
  id: string;
  label: string | null;
  labels: readonly string[];
  auto: boolean;
}) {
  const t = useTranslations('inbox');
  const { pending, error, run } = useAction();
  const selectId = useId();

  return (
    <div className="flex flex-col items-end gap-1 phone:items-start">
      <label htmlFor={selectId} className="sr-only">
        {t('changeLabel')}
      </label>
      <Select
        touch
        id={selectId}
        size="sm"
        className="w-48"
        value={label ?? ''}
        disabled={pending}
        onChange={(e) => {
          const next = e.target.value;
          run(() => relabelAction(id, next));
        }}
      >
        {label === null && <option value="">{t('label.none')}</option>}
        {labels.map((l) => (
          <option key={l} value={l}>
            {t(`label.${l}` as 'label.other')}
          </option>
        ))}
      </Select>
      {auto && !error && (
        <span className="text-small text-accent-ink flex items-center gap-1">
          <Sparkles aria-hidden="true" className="size-3" />
          {t('autoLabel')}
        </span>
      )}
      {error && (
        <span role="alert" className="text-negative text-small">
          {t('relabelError')}
        </span>
      )}
    </div>
  );
}
