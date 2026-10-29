'use client';

import { useTranslations } from 'next-intl';
import { type ReactNode, useId } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import type { Choice } from '@/lib/automations/editor-api';

export function Labelled({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: (ids: { id: string; describedBy?: string }) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, describedBy: hint ? hintId : undefined })}
      {hint && (
        <p id={hintId} className="text-small text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

export function ChoiceField({
  label,
  hint,
  value,
  choices,
  onChange,
}: {
  label: string;
  hint?: ReactNode;
  value: string;
  choices: readonly Choice[];
  onChange: (id: string) => void;
}) {
  const t = useTranslations('automationEditor.fields');
  const gone = value !== '' && !choices.some((c) => c.id === value);
  return (
    <Labelled label={label} hint={hint}>
      {({ id, describedBy }) => (
        <Select
          id={id}
          aria-describedby={describedBy}
          touch
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{t('pick')}</option>
          {gone && <option value={value}>{t('unavailable')}</option>}
          {choices.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
      )}
    </Labelled>
  );
}

const toNumber = (raw: string) => (raw.trim() === '' ? Number.NaN : Number(raw));

export function NumberField({
  label,
  hint,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  hint?: ReactNode;
  value: number;
  min: number;
  max: number;
  step?: number | 'any';
  onChange: (n: number) => void;
}) {
  return (
    <Labelled label={label} hint={hint}>
      {({ id, describedBy }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          type="number"
          inputMode={Number.isInteger(step) ? 'numeric' : 'decimal'}
          min={min}
          max={max}
          step={step}
          className="w-28 phone:h-11"
          value={Number.isNaN(value) ? '' : value}
          onChange={(e) => onChange(toNumber(e.target.value))}
        />
      )}
    </Labelled>
  );
}
