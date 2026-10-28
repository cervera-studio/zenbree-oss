'use client';

import { ShieldAlert } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import type { ListRow } from '@/lib/lists/api';

// Reasons app.crm.gate writes without an end date; anything newer reads as the generic one.
const CRM_HOLD_REASONS = [
  'HUBSPOT_OPTED_OUT',
  'HUBSPOT_CUSTOMER',
  'HUBSPOT_OPEN_DEAL',
  'HUBSPOT_OWNED',
] as const;

/**
 * Why the HubSpot read gate holds this person (#120): campaigns do not write to them. The reason is the
 * badge's title; `detail` writes it out beside the badge, where there is room for a sentence.
 */
export function CrmHold({
  hold,
  detail = false,
}: {
  hold: NonNullable<ListRow['crm_hold']>;
  detail?: boolean;
}) {
  const t = useTranslations('lists.crmHold');
  const format = useFormatter();
  const until = hold.until ? format.dateTime(new Date(hold.until), { dateStyle: 'medium' }) : null;
  const known = CRM_HOLD_REASONS.find((reason) => reason === hold.reason);
  const why =
    hold.reason === 'HUBSPOT_COOLDOWN'
      ? t('reasons.HUBSPOT_COOLDOWN', { until: until ?? '' })
      : t(`reasons.${known ?? 'other'}`);
  const badge = (
    <Badge tone="warning" className="w-fit gap-1" title={detail ? undefined : why}>
      <ShieldAlert className="size-3" aria-hidden />
      {/* An opt-out never lifts: it is a block, not a pause. */}
      {t(hold.reason === 'HUBSPOT_OPTED_OUT' ? 'blocked' : 'label')}
      {!detail && <span className="sr-only">: {why}</span>}
    </Badge>
  );
  if (!detail) return badge;
  return (
    <p className="text-muted flex flex-col items-start gap-1 text-xs-plus">
      {badge}
      {why}
    </p>
  );
}
