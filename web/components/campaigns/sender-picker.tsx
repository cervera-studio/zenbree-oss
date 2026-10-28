'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { setCampaignSendersAction } from '@/app/(app)/campaigns/actions';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { useErrorText } from '@/i18n/error-text';
import type { CampaignSenders } from '@/lib/campaigns/senders';
import { useAction } from '@/lib/hooks/use-action';
import { senderTone } from '@/lib/integrations/sender-state';
import { cn } from '@/lib/utils';

export function SenderPicker({
  campaignId,
  pool,
  keepOne,
}: {
  campaignId: string;
  /** `null`: the accounts failed to load. */
  pool: CampaignSenders | null;
  /** A live campaign with email steps: the API refuses to drop its last account that can send. */
  keepOne: boolean;
}) {
  const t = useTranslations('campaignBuilder.senders');
  const ts = useTranslations('senders');
  const errorText = useErrorText();
  const id = useId();
  const { pending, error, run } = useAction();

  if (pool === null) return <p className="text-negative text-ui">{t('loadError')}</p>;
  const { senders, maxSenders } = pool;
  if (senders.length === 0) {
    return (
      <div className="border-negative bg-negative-tint text-ui flex flex-col gap-1 rounded-lg border p-3">
        <p className="text-ink">{t('none')}</p>
        <Link
          href="/settings/integrations"
          className="text-accent-ink text-ui-strong hover:underline"
        >
          {t('connect')}
        </Link>
      </div>
    );
  }

  const picked = senders.filter((s) => s.picked).map((s) => s.id);
  const full = picked.length >= maxSenders;
  const ready = senders.filter((s) => s.picked && s.blocker === null);
  const kept = keepOne && ready.length === 1 ? ready[0]?.id : undefined;

  function toggle(sender: string, on: boolean) {
    const next = on ? [...picked, sender] : picked.filter((p) => p !== sender);
    run(() => setCampaignSendersAction(campaignId, next));
  }

  return (
    <div className="flex flex-col gap-2.5">
      {kept && <p className="text-muted text-small">{t('keepOne')}</p>}
      {full && <p className="text-muted text-small">{t('max', { max: maxSenders })}</p>}
      {senders.map((s) => {
        const connected = s.status === 'connected';
        const reconnect = s.blocker === 'disconnected' || s.blocker === 'needs_reconnect';
        return (
          <div
            key={s.id}
            className={cn(
              'border-line flex items-start gap-2.5 rounded-lg border p-3',
              connected ? 'bg-ground' : 'bg-raised',
            )}
          >
            <Checkbox
              id={`${id}-${s.id}`}
              className="mt-0.5"
              checked={s.picked}
              disabled={pending || s.id === kept || (!s.picked && (!connected || full))}
              aria-describedby={`${id}-${s.id}-state`}
              onCheckedChange={(v) => toggle(s.id, v === true)}
            />
            <div className="flex min-w-0 flex-col gap-1">
              <label htmlFor={`${id}-${s.id}`} className="text-ui-strong text-ink block truncate">
                {s.accountLabel ?? s.provider}
              </label>
              <div
                id={`${id}-${s.id}-state`}
                className="flex flex-wrap items-center gap-x-2 gap-y-1"
              >
                <Badge tone={senderTone(s.state, s.reason)} dot>
                  {s.reason ? ts(`reason.${s.reason}`) : ts('ready')}
                </Badge>
                <span className="text-muted text-small">
                  {ts('usage', { sent: s.sentToday, cap: s.dailyCap })}
                  {s.warmingUp && ` · ${t('warming')}`}
                </span>
              </div>
              {reconnect && (
                <Link
                  href="/settings/integrations"
                  className="text-negative text-small hover:underline"
                >
                  {ts('reconnect')}
                </Link>
              )}
            </div>
          </div>
        );
      })}
      {error && (
        <p role="alert" className="text-negative text-small">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}
