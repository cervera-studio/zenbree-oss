'use client';

import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldLabel } from '@/components/ui/field';
import { Select } from '@/components/ui/select';
import type { ListColumn } from '@/lib/lists/api';
import type { InstantlyCampaign, InstantlyMapping } from '@/lib/lists/instantly';

const OPTION =
  'has-checked:border-accent has-checked:bg-accent-tint border-line bg-ground has-focus-visible:ring-accent text-ui flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 has-focus-visible:ring-2';

/** Instantly's own number: 1 is running, so adding people may start emails there. */
export function CampaignStep({
  campaigns,
  picked,
  onPick,
}: {
  campaigns: InstantlyCampaign[];
  picked: string | null;
  onPick: (id: string) => void;
}) {
  const t = useTranslations('lists.instantly');
  if (campaigns.length === 0) return <p className="text-ui">{t('noCampaigns')}</p>;
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="text-small mb-2">{t('campaignLegend')}</legend>
      {campaigns.map((c) => (
        <label key={c.id} className={OPTION}>
          <input
            type="radio"
            name="instantly-campaign"
            className="accent-accent size-4 shrink-0"
            checked={picked === c.id}
            onChange={() => onPick(c.id)}
          />
          <span className="min-w-0 grow break-words">{c.name}</span>
          <Badge tone={c.may_send ? 'warning' : 'neutral'}>{t(`campaignStatus.${label(c)}`)}</Badge>
        </label>
      ))}
    </fieldset>
  );
}

type Status =
  | 'draft'
  | 'active'
  | 'paused'
  | 'completed'
  | 'running_subsequences'
  | 'account_suspended'
  | 'accounts_unhealthy'
  | 'bounce_protect'
  | 'other';
const STATUS = new Set<string>([
  'draft',
  'active',
  'paused',
  'completed',
  'running_subsequences',
  'account_suspended',
  'accounts_unhealthy',
  'bounce_protect',
]);
// `status_label` is the API's English word; the text shown is ours, in the user's language.
function label(c: InstantlyCampaign): Status {
  return STATUS.has(c.status_label) ? (c.status_label as Status) : 'other';
}

const MAPPING_FIELDS = [
  ['first_name', 'first_name'],
  ['last_name', 'last_name'],
  ['company_name', 'company_name'],
  ['job_title', 'title'],
  ['website', 'company_domain'],
  ['phone', 'phone'],
  ['personalization', ''],
] as const;
const VARIABLE = /^[a-z][a-z0-9_]{0,63}$/;
const MAX_CUSTOM = 20;

/** Columns that may leave Zenbree as a custom variable: the API refuses ids, contact statuses and the email. */
function sendableColumns(columns: ListColumn[]): ListColumn[] {
  return columns.filter(
    (c) =>
      c.key !== 'email' &&
      !c.key.endsWith('_id') &&
      !c.key.endsWith('_status') &&
      !c.key.endsWith('_source') &&
      VARIABLE.test(c.key),
  );
}

const LEAVE_OUT = '-';

/** A field with no entry takes the API default, and the names fall back on `full_name` there. */
export function defaultMapping(columns: ListColumn[]): InstantlyMapping {
  const keys = new Set(columns.map((c) => c.key));
  return {
    fields: Object.fromEntries(MAPPING_FIELDS.filter(([, def]) => keys.has(def))),
    custom: [],
  };
}

/** The wire form: an empty key leaves a field out, an absent one takes the default. */
export function toApiMapping(mapping: InstantlyMapping): InstantlyMapping {
  return {
    ...mapping,
    fields: Object.fromEntries(
      Object.entries(mapping.fields).map(([f, key]) => [f, key === LEAVE_OUT ? '' : key]),
    ),
  };
}

export function MappingStep({
  columns,
  mapping,
  onChange,
}: {
  columns: ListColumn[];
  mapping: InstantlyMapping;
  onChange: (next: InstantlyMapping) => void;
}) {
  const t = useTranslations('lists.instantly');
  const base = useId();
  const usable = sendableColumns(columns);
  const toggle = (key: string, on: boolean) =>
    onChange({
      ...mapping,
      custom: on ? [...mapping.custom, key] : mapping.custom.filter((k) => k !== key),
    });
  return (
    <div className="flex flex-col gap-4">
      <p className="text-muted text-small">{t('mappingHint')}</p>
      {MAPPING_FIELDS.map(([field]) => (
        <Field key={field}>
          <FieldLabel htmlFor={`${base}-${field}`}>{t(`field.${field}`)}</FieldLabel>
          <Select
            id={`${base}-${field}`}
            touch
            value={mapping.fields[field] ?? ''}
            onChange={(e) => {
              const rest = Object.entries(mapping.fields).filter(([f]) => f !== field);
              onChange({
                ...mapping,
                fields: Object.fromEntries(
                  e.target.value ? [...rest, [field, e.target.value]] : rest,
                ),
              });
            }}
          >
            <option value="">{t('automatic')}</option>
            <option value={LEAVE_OUT}>{t('leaveOut')}</option>
            {usable.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </Select>
        </Field>
      ))}
      {usable.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-small mb-1">{t('customLegend')}</legend>
          <p className="text-muted text-small">{t('customHint', { max: MAX_CUSTOM })}</p>
          {usable.map((c) => {
            const on = mapping.custom.includes(c.key);
            return (
              <div key={c.key} className="flex items-center gap-2.5">
                <Checkbox
                  id={`${base}-c-${c.key}`}
                  checked={on}
                  disabled={!on && mapping.custom.length >= MAX_CUSTOM}
                  onCheckedChange={(v) => toggle(c.key, v === true)}
                />
                <label htmlFor={`${base}-c-${c.key}`} className="text-ui min-w-0 break-words">
                  {c.label}
                </label>
              </div>
            );
          })}
        </fieldset>
      )}
    </div>
  );
}
