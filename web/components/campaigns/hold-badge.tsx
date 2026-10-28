import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import type { Target } from '@/lib/campaigns/api';
import { HOLD_BADGES, holdBadge } from '@/lib/campaigns/detail-view';
import { SENDER_HOLD_TONES } from '@/lib/campaigns/mailbox-state';

export function HoldBadge({ target }: { target: Target }) {
  const reason = holdBadge(target);
  if (reason) return <PersonHold reason={reason} />;
  if (target.senderHold) return <SenderHold hold={target.senderHold} sender={target.sender} />;
  return null;
}

function PersonHold({ reason }: { reason: NonNullable<ReturnType<typeof holdBadge>> }) {
  const t = useTranslations('campaigns.detail.hold');
  const badge = <Badge tone={HOLD_BADGES[reason]}>{t(`${reason}.label`)}</Badge>;
  return (
    <span className="flex basis-full flex-col items-start gap-0.5">
      {reason === 'stop_failed' ? (
        <Link href="/settings/integrations" className="hover:underline">
          {badge}
        </Link>
      ) : (
        badge
      )}
      <span className="text-data-sm text-muted">{t(`${reason}.hint`)}</span>
    </span>
  );
}

function SenderHold({
  hold,
  sender,
}: {
  hold: NonNullable<Target['senderHold']>;
  sender: string | null;
}) {
  const t = useTranslations('campaigns.senderHold');
  return (
    <span className="flex basis-full flex-col items-start gap-0.5">
      <Badge tone={SENDER_HOLD_TONES[hold]}>{t('label')}</Badge>
      <span className="text-data-sm text-muted">
        {t(`reasons.${hold}`, { sender: sender ?? '' })}
      </span>
    </span>
  );
}
