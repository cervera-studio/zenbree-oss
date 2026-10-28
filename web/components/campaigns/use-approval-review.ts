'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { decideSendsAction, editSendAction, undoRejectAction } from '@/app/(app)/campaigns/actions';
import { toast } from '@/components/ui/sonner';
import type { PendingSend } from '@/lib/campaigns/api';
import { useAction } from '@/lib/hooks/use-action';
import type { Verb } from './approval-card';
import { afterDecision, afterUndo, reviewQueue, withIds, withoutIds } from './approval-progress';
import { isHeld } from './held-sends';

/** One review visit over the server's queue, shared by the campaign page and the approval screen. */
export function useApprovalReview(campaignId: string, items: PendingSend[]) {
  const t = useTranslations('campaigns.approvals');
  const tc = useTranslations('common');
  const { pending, error, run } = useAction();
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set());
  const [counts, setCounts] = useState<Record<Verb, number>>({ approve: 0, reject: 0 });
  const [held, setHeld] = useState(0);
  // The done state follows any decision, also one the API found already made elsewhere.
  const [acted, setActed] = useState(false);
  // What the API answered held, until the refreshed queue carries the hold itself.
  const [justHeld, setJustHeld] = useState({ ids: new Set<string>(), of: items });
  const heldIds = new Set([
    ...(justHeld.of === items ? justHeld.ids : []),
    ...items.filter(isHeld).map((i) => i.id),
  ]);
  const review = reviewQueue(
    items.filter((i) => !heldIds.has(i.id)),
    withoutIds(decided, [...heldIds]),
  );
  const heldItems = items.filter(isHeld);

  // P1 (#230): a reject acts at once and offers «Annulla»; the API restores only who has not moved on.
  const undo = (ids: string[]) =>
    run(() => undoRejectAction(campaignId, ids), {
      onSuccess: ({ restored }) => {
        setDecided((d) => afterUndo(d, ids, restored));
        setCounts((c) => ({ ...c, reject: Math.max(0, c.reject - restored) }));
        if (restored === ids.length) toast.success(t('restored', { count: restored }));
        else toast.warning(t('undoLate', { count: ids.length - restored }));
      },
    });

  const decide = (verb: Verb, ids: string[]) => {
    // Outside the transition, so the message leaves the screen on the click.
    setDecided((d) => withIds(d, ids));
    setActed(true);
    let settled = false;
    void run(() => decideSendsAction(campaignId, verb, ids), {
      onSuccess: (data) => {
        settled = true;
        const count = (verb === 'approve' ? data.approved : data.rejected) ?? 0;
        const waiting = verb === 'approve' ? (data.held ?? 0) : 0;
        const leftReview = count + waiting;
        setDecided((d) => afterDecision(d, ids, leftReview));
        setHeld((h) => h + waiting);
        // Which ones of a partly held batch is known only after the refresh.
        if (waiting > 0 && waiting === ids.length) setJustHeld({ ids: new Set(ids), of: items });
        setCounts((c) => ({ ...c, [verb]: c[verb] + count }));
        // Nothing skipped (decided elsewhere meanwhile): no success toast, nothing to undo.
        if (verb === 'reject' && count > 0) {
          toast.success(t('rejected', { count }), {
            action: { label: tc('undo'), onClick: () => undo(ids) },
          });
        }
      },
    }).then(() => {
      // A failure, or an action that threw: the messages still wait.
      if (!settled) setDecided((d) => withoutIds(d, ids));
    });
  };

  const save = (id: string) => (subject: string, body: string) =>
    run(() => editSendAction(campaignId, id, subject, body));

  const view = review.current ? 'reviewing' : acted ? 'done' : 'empty';
  return { ...review, counts, held, heldItems, view, pending, error, decide, save } as const;
}
