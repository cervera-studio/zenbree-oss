'use client';

import { Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Panel } from '@/components/ui/panel';
import { SegmentedControl } from '@/components/ui/segmented-control';
import {
  type CampaignField,
  entryOf,
  type FieldCatalog,
  MAX_FALLBACK,
  usesToken,
} from '@/lib/campaigns/fields';
import type { Step } from '@/lib/campaigns/steps';
import { useFieldLabel } from './field-picker';

export function CampaignFields({
  fields,
  steps,
  catalog,
  editable,
  onChange,
}: {
  fields: CampaignField[];
  steps: Step[];
  catalog: FieldCatalog | null;
  editable: boolean;
  onChange: (fields: CampaignField[]) => void;
}) {
  const t = useTranslations('campaignBuilder.fields');
  const label = useFieldLabel(catalog);
  const set = (i: number, patch: Partial<CampaignField>) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <Panel title={t('title')} meta={t('meta')}>
      {fields.length === 0 ? (
        <p className="text-muted text-ui">{t('none')}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {fields.map((field, i) => (
            <FieldRow
              key={field.token}
              field={field}
              label={label(field)}
              filled={entryOf(field, catalog)?.filled}
              people={catalog?.people}
              inSteps={usesToken(steps, field.token)}
              editable={editable}
              onChange={(patch) => set(i, patch)}
              onRemove={() => onChange(fields.filter((_, j) => j !== i))}
            />
          ))}
        </ul>
      )}
      {editable && fields.length > 0 && <p className="text-muted text-small mt-3">{t('help')}</p>}
    </Panel>
  );
}

function FieldRow({
  field,
  label,
  filled,
  people,
  inSteps,
  editable,
  onChange,
  onRemove,
}: {
  field: CampaignField;
  label: string;
  filled: number | undefined;
  people: number | undefined;
  inSteps: boolean;
  editable: boolean;
  onChange: (patch: Partial<CampaignField>) => void;
  onRemove: () => void;
}) {
  const t = useTranslations('campaignBuilder.fields');
  const id = useId();
  const fallback = typeof field.fallback === 'string';
  const blank = fallback && !field.fallback?.trim();
  return (
    <li className="border-line flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-ui-strong text-ink truncate">{label}</p>
          <p className="text-muted text-data-sm truncate font-mono">
            {`{{${field.token}}}`}
            {filled !== undefined && people !== undefined && (
              <span className="font-sans">
                {' · '}
                {t('filled', { filled, people })}
              </span>
            )}
          </p>
        </div>
        {editable && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={inSteps}
            onClick={onRemove}
            aria-label={t('remove', { field: label })}
          >
            <Trash2 className="size-4" />
          </Button>
        )}
      </div>
      <SegmentedControl
        label={t('mode', { field: label })}
        disabled={!editable}
        options={[
          { value: 'required', label: t('required') },
          { value: 'fallback', label: t('fallback') },
        ]}
        value={fallback ? 'fallback' : 'required'}
        onChange={(mode) => onChange({ fallback: mode === 'fallback' ? '' : null })}
      />
      {fallback && (
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-fallback`} className="sr-only">
            {t('fallbackLabel', { field: label })}
          </label>
          <Input
            id={`${id}-fallback`}
            value={field.fallback ?? ''}
            maxLength={MAX_FALLBACK}
            disabled={!editable}
            placeholder={t('fallbackPlaceholder')}
            aria-invalid={blank || undefined}
            aria-describedby={blank ? `${id}-blank` : undefined}
            onChange={(e) => onChange({ fallback: e.target.value })}
          />
          {blank && (
            <p id={`${id}-blank`} className="text-negative text-small">
              {t('fallbackBlank')}
            </p>
          )}
        </div>
      )}
    </li>
  );
}
