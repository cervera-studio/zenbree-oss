'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useRef, useState } from 'react';
import { AppPage } from '@/components/app/page';
import { Button } from '@/components/ui/button';
import { useErrorText } from '@/i18n/error-text';
import type { PendingSend } from '@/lib/campaigns/api';
import { EditFields, fullName } from './approval-card';
import { MessageCard, ReviewDone } from './approval-review';
import { HeldNote } from './held-note';
import { HeldSends } from './held-sends';
import { ProgressBar } from './progress-bar';
import { ReviewActions } from './review-actions';
import { ReviewNext } from './review-next';
import { ReviewedCount } from './reviewed-count';
import { useApprovalReview } from './use-approval-review';

/** Da approvare (#864): one waiting message at a time, with the queue after it on the side. */
export function ApprovalScreen({
  campaign,
  items,
  loadFailed,
}: {
  campaign: { id: string; name: string };
  items: PendingSend[];
  loadFailed: boolean;
}) {
  const t = useTranslations('campaigns');
  const errorText = useErrorText();
  const review = useApprovalReview(campaign.id, items);
  const { current, counts, pending } = review;
  // Keyed on the message: the editor closes by itself when another message comes up.
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = current !== undefined && editingId === current.id;
  const editRef = useRef<HTMLButtonElement>(null);
  const back = `/campaigns/${campaign.id}`;

  const closeEditor = () => {
    setEditingId(null);
    editRef.current?.focus();
  };

  return (
    <AppPage
      crumbs={[
        { label: t('title'), href: '/campaigns' },
        { label: campaign.name, href: back },
        { label: t('approvals.reviewTitle') },
      ]}
      crumbsLabel={t('breadcrumb')}
      topbarActions={
        <span className="text-small text-muted tabular-nums">
          {t('approvals.decided', { approved: counts.approve, skipped: counts.reject })}
        </span>
      }
      title={t('approvals.reviewTitle')}
      meta={campaign.name}
      actions={
        review.total > 0 && (
          <div className="flex items-center gap-3 phone:w-full">
            <span className="text-data text-muted shrink-0 font-mono tabular-nums">
              <ReviewedCount reviewed={review.reviewed} total={review.total} />
            </span>
            <div className="w-50 phone:grow">
              <ProgressBar
                label={t('approvals.progress', {
                  current: Math.min(review.reviewed + 1, review.total),
                  total: review.total,
                })}
                value={review.reviewed}
                max={review.total}
              />
            </div>
          </div>
        )
      }
    >
      {/* Mounted from the start, so a screen reader hears each new message, not only the first. */}
      <p aria-live="polite" className="sr-only">
        {current ? t('approvals.announce', { name: fullName(current) }) : ''}
      </p>
      {review.error && (
        <p role="alert" className="text-negative text-sm">
          {errorText(review.error)}
        </p>
      )}
      <HeldNote held={review.held} peopleHref={back} />
      {loadFailed ? (
        <p className="text-negative text-sm">{t('approvals.loadError')}</p>
      ) : current ? (
        <div className="grid grid-cols-1 items-start gap-4 desktop:grid-cols-[minmax(0,1fr)_21.25rem]">
          <MessageCard
            item={current}
            editor={
              editing ? (
                <EditFields
                  item={current}
                  onSave={review.save(current.id)}
                  onDone={closeEditor}
                  autoFocus
                />
              ) : undefined
            }
          >
            <ReviewActions
              blocked={pending || editing}
              onDecide={(verb) => review.decide(verb, [current.id])}
              onEdit={() => setEditingId(current.id)}
              editRef={editRef}
            />
          </MessageCard>
          <ReviewNext next={review.next} />
        </div>
      ) : review.view === 'done' ? (
        <ReviewDone approved={counts.approve}>
          <Button asChild variant="outline" size="lg" className="w-full">
            <Link href={back}>{t('approvals.backToCampaign')}</Link>
          </Button>
        </ReviewDone>
      ) : (
        <div className="flex flex-col items-start gap-3">
          <p className="text-muted text-ui">{t('approvals.empty')}</p>
          <Button asChild variant="outline">
            <Link href={back}>{t('approvals.backToCampaign')}</Link>
          </Button>
        </div>
      )}
      {!loadFailed && <HeldSends items={review.heldItems} />}
    </AppPage>
  );
}
