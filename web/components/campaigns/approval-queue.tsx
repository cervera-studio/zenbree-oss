'use client';

import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/ui/panel';
import { useErrorText } from '@/i18n/error-text';
import type { PendingSend } from '@/lib/campaigns/api';
import { cn } from '@/lib/utils';
import { PendingCard } from './approval-card';
import { PhoneReview, ReviewDone } from './approval-review';
import { HeldNote } from './held-note';
import { HeldSends } from './held-sends';
import { useApprovalReview } from './use-approval-review';

// C3 shows one row of cards; approving them brings the next ones in.
const SHOWN = 3;

export function ApprovalQueue({
  campaignId,
  items,
  loadFailed,
  active = false,
}: {
  campaignId: string;
  items: PendingSend[];
  loadFailed: boolean;
  // An active campaign queues more each day: the empty queue says so.
  active?: boolean;
}) {
  const t = useTranslations('campaigns.approvals');
  const errorText = useErrorText();
  const review = useApprovalReview(campaignId, items);
  const { pending, error, decide, save, counts, current: first } = review;
  // Phone (#171, M4): one message at a time, the first still waiting, with progress.
  const phoneView = review.view;
  const shown = review.queue.slice(0, SHOWN);

  const title = t('title', { count: review.queue.length });
  const notice = (
    <>
      {loadFailed && <p className="text-negative text-sm">{t('loadError')}</p>}
      {!loadFailed && !first && (
        <p className={cn('text-muted text-sm', phoneView === 'done' && 'phone:hidden')}>
          {active ? t('emptyNext') : t('empty')}
        </p>
      )}
    </>
  );

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p role="alert" className="text-negative text-sm">
          {errorText(error)}
        </p>
      )}
      <HeldNote held={review.held} />
      <div className="phone:hidden">
        <Panel
          title={title}
          action={
            shown.length > 1 && (
              // Names exactly the messages on screen: anything claimed since stays waiting.
              <Button
                variant="link"
                size="sm"
                disabled={pending}
                onClick={() =>
                  decide(
                    'approve',
                    shown.map((i) => i.id),
                  )
                }
              >
                {t('approveAll', { count: shown.length })}
              </Button>
            )
          }
        >
          <div className="flex flex-col gap-3">
            <p className="text-muted text-ui">{t('description')}</p>
            {notice}
            {shown.length > 0 && (
              <ul className="flex flex-col gap-2.5">
                {shown.map((item) => (
                  <li key={item.id}>
                    <PendingCard
                      item={item}
                      pending={pending}
                      onDecide={(verb) => decide(verb, [item.id])}
                      onSave={save(item.id)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>
      {/* The Panel above names the desktop region; it is hidden here, so the phone view names its own. */}
      <section aria-label={title} className="flex flex-col gap-3 desktop:hidden">
        <h2 className="text-ui-strong">{title}</h2>
        <p className="text-muted text-ui">{t('description')}</p>
        {notice}
        {!loadFailed && phoneView === 'done' && <ReviewDone approved={counts.approve} />}
        {first && (
          <PhoneReview
            // Remounts per message, so the edit sheet starts from that message's text.
            key={first.id}
            item={first}
            reviewed={review.reviewed}
            total={review.total}
            decided={counts}
            pending={pending}
            onDecide={(verb) => decide(verb, [first.id])}
            onSave={save(first.id)}
          />
        )}
      </section>
      <HeldSends items={review.heldItems} />
    </div>
  );
}
