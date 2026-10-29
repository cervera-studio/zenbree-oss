import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Panel } from '@/components/ui/panel';
import type { PendingSend } from '@/lib/campaigns/api';
import { HOLD_BADGES, type HoldReason } from '@/lib/campaigns/detail-view';
import { fullName } from './approval-card';
import { StepIcon } from './step-icon';

type Held = PendingSend & { hold: HoldReason };

export function HeldSends({ items }: { items: Held[] }) {
  const t = useTranslations('campaigns');
  if (items.length === 0) return null;
  return (
    <Panel
      title={t('approvals.heldTitle')}
      meta={t('approvals.heldMeta', { count: items.length })}
      flush
    >
      <p className="text-muted text-small border-hair border-b px-3 py-2.5">
        {t('approvals.heldDescription')}
      </p>
      <ul>
        {items.map((item) => (
          <li
            key={item.id}
            className="border-hair flex items-start gap-2.5 border-b px-3 py-2.5 last:border-b-0"
          >
            <StepIcon channel={item.channel} />
            <span className="flex min-w-0 grow flex-col gap-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-ui-strong text-ink truncate">{fullName(item)}</span>
                <Badge tone={HOLD_BADGES[item.hold]}>{t(`detail.hold.${item.hold}.label`)}</Badge>
              </span>
              <span className="text-small text-muted">{t(`detail.hold.${item.hold}.hint`)}</span>
            </span>
            <span className="text-data-sm text-muted shrink-0 font-mono">
              {t('approvals.stepShort', { step: item.step })}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export function isHeld(item: PendingSend): item is Held {
  return item.hold !== null;
}
