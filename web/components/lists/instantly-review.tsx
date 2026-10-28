'use client';

import { ExternalLink } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { useErrorText } from '@/i18n/error-text';
import { OUTCOME_TONE, OUTCOMES } from '@/lib/integrations/instantly-campaign';
import type { InstantlyEnrollment, InstantlyPreview } from '@/lib/lists/instantly';

// A reason from the recheck is a lower-case word; one from a failed call is an error code.
// A batch holds up to 1000 rows: the list shows the first ones and the counts above cover them all.
const SHOWN_ROWS = 200;
const CODE = /^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$/;
const REASONS = new Set([
  'excluded',
  'no_email',
  'duplicate_in_selection',
  'replied_or_contacted',
  'in_native_campaign',
  'already_in_instantly',
  'stopped',
  'crm_hold',
  'provider_skipped',
  'email_changed',
  'cancelled',
  'superseded',
  'cancelled_in_flight',
]);

function useReasonText() {
  const t = useTranslations('lists.instantly');
  const errorText = useErrorText();
  return (reason: string | null): string | null => {
    if (!reason) return null;
    if (CODE.test(reason)) return errorText({ code: reason });
    return t(`reason.${REASONS.has(reason) ? reason : 'other'}` as 'reason.other');
  };
}

function ExternalLinkButton({ href, children }: { href: string; children: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent-ink text-ui-strong inline-flex items-center gap-1"
    >
      {children}
      <ExternalLink aria-hidden className="size-3.5" />
    </a>
  );
}

export function PreviewStep({
  preview,
  acknowledged,
  onAcknowledge,
}: {
  preview: InstantlyPreview;
  acknowledged: boolean;
  onAcknowledge: (on: boolean) => void;
}) {
  const t = useTranslations('lists.instantly');
  const reasonText = useReasonText();
  const ackId = useId();
  const reasons = Object.entries(preview.skipped).filter(([, n]) => n > 0);
  return (
    <div className="flex flex-col gap-3">
      <strong className="text-feature break-words">
        {t('willSend', { n: preview.eligible, name: preview.campaign.name })}
      </strong>
      <p className="text-muted text-small">{t('selectedOf', { n: preview.selected })}</p>
      {preview.campaign.may_send && (
        <div role="alert" className="bg-warning-tint text-ui flex flex-col gap-2 rounded-lg p-3.5">
          <p>{t('sendingWarning')}</p>
          <div className="flex items-start gap-2.5">
            <Checkbox
              id={ackId}
              checked={acknowledged}
              onCheckedChange={(v) => onAcknowledge(v === true)}
            />
            <label htmlFor={ackId}>{t('sendingAcknowledge')}</label>
          </div>
        </div>
      )}
      {preview.custom_variables.length > 0 && (
        <p className="text-muted text-small">
          {t('customAdded', { names: preview.custom_variables.join(', ') })}
        </p>
      )}
      {reasons.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {reasons.map(([reason, n]) => (
            <li key={reason} className="text-ui rounded-lg border px-3 py-2">
              {t('skippedReason', { n, reason: reasonText(reason) ?? '' })}
            </li>
          ))}
        </ul>
      )}
      <ExternalLinkButton href={preview.campaign.url}>{t('openInstantly')}</ExternalLinkButton>
    </div>
  );
}

export function ResultStep({ enrollment }: { enrollment: InstantlyEnrollment }) {
  const t = useTranslations('lists.instantly');
  const errorText = useErrorText();
  const reasonText = useReasonText();
  const counts = OUTCOMES.filter((o) => (enrollment.counts[o] ?? 0) > 0);
  return (
    <div className="flex flex-col gap-3" role="status">
      <strong className="text-feature break-words">
        {t(`status.${enrollment.status}`, { name: enrollment.campaign.name })}
      </strong>
      {enrollment.status === 'failed' && enrollment.error_code && (
        <p role="alert" className="bg-negative-tint text-ui rounded-lg p-3.5">
          {errorText({ code: enrollment.error_code })}
        </p>
      )}
      {enrollment.counts.uncertain ? (
        <p className="bg-warning-tint text-ui rounded-lg p-3.5">{t('uncertainHint')}</p>
      ) : null}
      <ul className="flex flex-wrap gap-2">
        {counts.map((o) => (
          <li key={o}>
            <Badge tone={OUTCOME_TONE[o]}>
              {t('count', { n: enrollment.counts[o] ?? 0, outcome: t(`outcome.${o}`) })}
            </Badge>
          </li>
        ))}
      </ul>
      <ExternalLinkButton href={enrollment.campaign.url}>{t('openInstantly')}</ExternalLinkButton>
      <ul className="flex flex-col gap-1.5" aria-label={t('rowsLabel')}>
        {enrollment.rows.slice(0, SHOWN_ROWS).map((r, i) => (
          <li
            key={r.row_id ?? `${r.email}-${i}`}
            className="text-ui flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border px-3 py-2"
          >
            <span className="min-w-0 grow break-all">{r.email ?? t('noEmail')}</span>
            <Badge tone={OUTCOME_TONE[r.outcome]}>{t(`outcome.${r.outcome}`)}</Badge>
            {r.reason && (
              <span className="text-muted text-small w-full">{reasonText(r.reason)}</span>
            )}
          </li>
        ))}
      </ul>
      {enrollment.rows.length > SHOWN_ROWS && (
        <p className="text-muted text-small">
          {t('moreRows', { n: enrollment.rows.length - SHOWN_ROWS })}
        </p>
      )}
    </div>
  );
}
