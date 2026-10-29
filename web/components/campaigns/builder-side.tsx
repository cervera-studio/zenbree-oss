'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { Select } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useErrorText } from '@/i18n/error-text';
import type { Preview } from '@/lib/campaigns/api';
import { VARIANTS, type Variant } from '@/lib/campaigns/steps';
import { cn } from '@/lib/utils';

// ponytail: read-only by owner decision on #217. The window is the mailboxes' fixed rule
// (`dispatcher.py`, `send_window_*` defaults: Monday to Friday, 9-18 Europe/Rome); no campaign field edits it.
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const; // Monday = 0, as `send_window_days`
const SENDING_DAYS = 5;

export function SendWindow() {
  const t = useTranslations('campaignBuilder.when');
  return (
    <Panel title={t('title')}>
      <div className="flex flex-col gap-2.5">
        <p className="sr-only">{t('days')}</p>
        <ul aria-hidden="true" className="flex gap-1">
          {WEEKDAYS.map((day) => (
            <li
              key={day}
              className={cn(
                'text-ui-strong flex size-7 items-center justify-center rounded-md',
                day < SENDING_DAYS ? 'bg-accent text-on-accent' : 'bg-raised text-muted',
              )}
            >
              {t('letters')[day]}
            </li>
          ))}
        </ul>
        <p className="text-small text-soft">{t('window')}</p>
        <p className="text-small text-muted">{t('fixed')}</p>
      </div>
    </Panel>
  );
}

export function ApprovalNote() {
  const t = useTranslations('campaignBuilder.approval');
  const id = useId();
  // ponytail: always on (owner decision on #217); no campaign field turns it off, so the switch is checked and disabled.
  return (
    <div className="border-line bg-ground flex items-center gap-2.5 rounded-lg border p-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p id={id} className="text-ui-strong text-ink">
          {t('title')}
        </p>
        <p className="text-small text-muted">{t('body')}</p>
      </div>
      <Switch checked disabled aria-labelledby={id} />
    </div>
  );
}

const previewError = (code: string) =>
  `previewErrors.${code.replace('.', '_')}` as 'previewErrors.NO_EMAIL';

export function StepPreview({
  items,
  abTest = false,
  step,
  person,
  onPerson,
  updating,
  failed,
  fieldName,
}: {
  items: Preview['items'];
  // The campaign tests copy B (#934): a switch shows one copy or the other.
  abTest?: boolean;
  step: number | null;
  person: number;
  onPerson: (index: number) => void;
  updating: boolean;
  failed: boolean;
  fieldName: (token: string) => string;
}) {
  const t = useTranslations('campaignBuilder.preview');
  const format = useFormatter();
  const tc = useTranslations('campaigns');
  const errorText = useErrorText();
  const tv = useTranslations('campaignBuilder.variants');
  const [picked, setPicked] = useState<Variant>('a');
  const variant = abTest ? picked : 'a';
  const item = items[Math.min(person, items.length - 1)];
  // No `stepsB` yet means no step has copy B: a person in variant B gets copy A.
  const copies = variant === 'b' ? (item?.stepsB ?? item?.steps) : item?.steps;
  const rendered = step === null ? undefined : copies?.find((s) => s.step === step);
  const name = (p: Preview['items'][number]['target']) =>
    [p.firstName, p.lastName].filter(Boolean).join(' ') || p.companyName || p.email || '—';

  const title = step === null ? t('plain') : t('title', { number: step });
  return (
    <Panel
      title={title}
      action={
        items.length > 0 && (
          <Select
            aria-label={t('person')}
            size="sm"
            value={Math.min(person, items.length - 1)}
            onChange={(e) => onPerson(Number(e.target.value))}
            className="max-w-48"
          >
            {items.map((it, i) => (
              <option key={`${i}-${name(it.target)}`} value={i}>
                {name(it.target)}
              </option>
            ))}
          </Select>
        )
      }
    >
      <div className="flex flex-col gap-2.5">
        {abTest && (
          <SegmentedControl
            label={tv('previewLabel')}
            options={VARIANTS.map((v) => ({ value: v, label: tv(v) }))}
            value={variant}
            onChange={setPicked}
          />
        )}
        <div className="border-line bg-raised text-small text-ink flex flex-col gap-2 rounded-lg border p-3">
          {items.length === 0 && <p className="text-muted">{t('empty')}</p>}
          {items.length > 0 && step === null && <p className="text-muted">{t('noSteps')}</p>}
          {item && item.missing.length > 0 && (
            <p className="text-warning">
              {t('missing', {
                fields: format.list(item.missing.map(fieldName)),
                count: item.missing.length,
              })}
            </p>
          )}
          {rendered?.error && (
            <p className="text-negative">
              {tc.has(previewError(rendered.error))
                ? tc(previewError(rendered.error))
                : errorText({ code: rendered.error })}
            </p>
          )}
          {rendered && !rendered.error && (
            <>
              {rendered.subject && <p className="text-ui-strong">{rendered.subject}</p>}
              <p className="whitespace-pre-line">{rendered.body}</p>
              {/* Added by the system on send, not part of the copy: muted, and outside the length checks. */}
              {(rendered.signOff || rendered.footer) && (
                <div className="text-muted flex flex-col gap-2">
                  {rendered.signOff && <p className="whitespace-pre-line">{rendered.signOff}</p>}
                  {rendered.footer && <p className="whitespace-pre-line">{rendered.footer}</p>}
                </div>
              )}
            </>
          )}
        </div>
        {/* Only this short status is announced; the preview copy itself would read out on every keystroke. */}
        <p
          role="status"
          className={cn('text-small', failed && !updating ? 'text-negative' : 'text-muted')}
        >
          {updating ? t('updating') : failed ? t('failed') : ''}
        </p>
      </div>
    </Panel>
  );
}
