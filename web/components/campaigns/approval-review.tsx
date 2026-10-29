'use client';

import { Check } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type ReactNode, useState } from 'react';
import { Tag } from '@/components/ui/badge';
import { BottomSheetContent } from '@/components/ui/bottom-sheet';
import { Button } from '@/components/ui/button';
import { Dialog, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { PendingSend } from '@/lib/campaigns/api';
import { EditFields, fullName, HeldLine, SendLine, type Verb } from './approval-card';
import { ProgressBar } from './progress-bar';
import { ReviewedCount } from './reviewed-count';
import { VariantTag } from './variant-tag';

/** The queue reviewed to the end: what leaves, and where the replies arrive. */
export function ReviewDone({ approved, children }: { approved: number; children?: ReactNode }) {
  const t = useTranslations('campaigns.approvals');
  return (
    <div role="status" className="flex flex-col items-center gap-3 py-10 text-center">
      <span
        aria-hidden="true"
        className="bg-positive-tint text-positive flex size-16 items-center justify-center rounded-2xl"
      >
        <Check className="size-8" />
      </span>
      <h3 className="text-feature">{t('doneTitle')}</h3>
      <p className="text-muted text-ui">{t('doneBody', { approved })}</p>
      <div className="mt-3 flex w-full max-w-sm flex-col gap-2">
        <Button asChild size="lg" className="w-full">
          <Link href="/inbox">{t('toInbox')}</Link>
        </Button>
        {children}
      </div>
    </div>
  );
}

/**
 * One waiting message as the reviewer reads it. The content remounts per message, so an editor in
 * `editor` starts from that message's text; `children` (the actions) stay mounted across messages.
 */
export function MessageCard({
  item,
  editor,
  children,
}: {
  item: PendingSend;
  editor?: ReactNode;
  children?: ReactNode;
}) {
  const t = useTranslations('campaigns');
  const initials = [item.firstName, item.lastName]
    .map((s) => s.charAt(0))
    .join('')
    .toUpperCase();
  return (
    <article className="border-line bg-ground flex flex-col gap-3 rounded-xl border p-4 desktop:p-6">
      <div key={item.id} className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="bg-accent-tint text-accent-ink flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-bold"
          >
            {initials || '—'}
          </span>
          <span className="flex min-w-0 grow flex-col">
            <span className="text-ui-strong text-ink">{fullName(item)}</span>
            {item.companyName && (
              <span className="text-muted text-small truncate">{item.companyName}</span>
            )}
          </span>
          <Tag tone="accent">
            {t('approvals.stepChannel', {
              step: item.step,
              channel: t(`step.channels.${item.channel}` as 'step.channels.email'),
            })}
          </Tag>
          <VariantTag variant={item.variant} />
        </div>
        {editor ?? (
          <>
            {item.subject && <p className="text-feature text-ink">{item.subject}</p>}
            <p className="text-body text-ink bg-raised rounded-lg p-3 whitespace-pre-line">
              {item.body}
            </p>
            <SendLine item={item} />
            <HeldLine item={item} />
          </>
        )}
      </div>
      {children}
    </article>
  );
}

/** M4: the first waiting message full width, its progress, an edit sheet and the actions fixed at the bottom. */
export function PhoneReview({
  item,
  reviewed,
  total,
  decided,
  pending,
  onDecide,
  onSave,
}: {
  item: PendingSend;
  reviewed: number;
  total: number;
  decided: Record<Verb, number>;
  pending: boolean;
  onDecide: (verb: Verb) => void;
  onSave: (subject: string, body: string) => void;
}) {
  const t = useTranslations('campaigns');
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-col gap-3 desktop:hidden">
      <div className="flex flex-col gap-1.5">
        <div className="text-muted text-small flex justify-between tabular-nums">
          <span>
            <ReviewedCount reviewed={reviewed} total={total} />
          </span>
          <span>
            {t('approvals.decided', { approved: decided.approve, skipped: decided.reject })}
          </span>
        </div>
        <ProgressBar
          label={t('approvals.progress', { current: reviewed + 1, total })}
          value={reviewed}
          max={total}
        />
      </div>
      <MessageCard item={item} />
      <Button variant="link" className="h-11" disabled={pending} onClick={() => setEditing(true)}>
        {t('approvals.editText')}
      </Button>
      <Dialog open={editing} onOpenChange={setEditing}>
        <BottomSheetContent aria-describedby={undefined} onDismiss={() => setEditing(false)}>
          <DialogHeader>
            <DialogTitle>{t('approvals.editTitle', { name: fullName(item) })}</DialogTitle>
          </DialogHeader>
          <EditFields item={item} onSave={onSave} onDone={() => setEditing(false)} />
        </BottomSheetContent>
      </Dialog>
      <div
        data-phone-bar
        className="bg-ground fixed inset-x-0 bottom-0 z-10 flex gap-2 border-t px-4 pt-3 pb-5"
      >
        <Button
          variant="outline"
          size="lg"
          className="flex-1"
          disabled={pending}
          onClick={() => onDecide('reject')}
        >
          {t('approvals.skip')}
        </Button>
        <Button size="lg" className="flex-2" disabled={pending} onClick={() => onDecide('approve')}>
          {t('approvals.approve')}
        </Button>
      </div>
    </div>
  );
}
