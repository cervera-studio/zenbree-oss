import { Info } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { getTranslations } from 'next-intl/server';
import { StatusNotice } from '@/components/settings/status-notice';
import { Tag } from '@/components/ui/badge';
import { reminderMonth } from '@/lib/inbox/dates';

export async function Unsubscribed({ name }: { name: string }) {
  const t = await getTranslations('inbox');
  return (
    <div
      role="status"
      className="border-negative/30 bg-negative-tint text-ui flex shrink-0 items-start gap-2.5 rounded-lg border px-3.5 py-3"
    >
      <Info aria-hidden="true" className="text-negative mt-0.5 size-4 shrink-0" />
      <span>
        {t('unsubscribedState', { name })}{' '}
        <Link
          href="/settings/exclusions"
          className="text-accent-ink text-ui-strong hover:underline"
        >
          {t('seeExclusions')}
        </Link>
      </span>
    </div>
  );
}

export function UnverifiedMarker({ authenticated }: { authenticated: boolean | null }) {
  const t = useTranslations('inbox.unverified');
  if (authenticated !== false) return null;
  return <Tag tone="warning">{t('marker')}</Tag>;
}

export function UnverifiedNotice({ authenticated }: { authenticated: boolean | null }) {
  const t = useTranslations('inbox.unverified');
  if (authenticated !== false) return null;
  return <StatusNotice tone="warning">{t('notice')}</StatusNotice>;
}

// A reply filed under a campaign can have no target (a colleague wrote to the campaign's address),
// and then no sequence stopped: the claim needs the person's target, not the campaign.
export function SequenceStopped({
  message,
  name,
}: {
  message: { kind: string; targetId: string | null };
  name: string;
}) {
  const t = useTranslations('inbox');
  if (message.kind !== 'reply' || !message.targetId) return null;
  return <p className="text-muted text-small text-center">{t('sequenceStopped', { name })}</p>;
}

// The sequence sentence of the reminder needs a target too: a colleague's reply has none.
export function ReminderSet({
  hasTarget,
  name,
  remindOn,
}: {
  hasTarget: boolean;
  name: string;
  remindOn: string;
}) {
  const t = useTranslations('inbox.reminder');
  const format = useFormatter();
  return (
    <>
      {t(hasTarget ? 'set' : 'setNoSequence', {
        name,
        month: reminderMonth(format, remindOn, new Date()),
      })}
    </>
  );
}
