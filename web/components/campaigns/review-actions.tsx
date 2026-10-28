'use client';

import { Pencil } from 'lucide-react';
import { useTranslations } from 'next-intl';
import {
  type MouseEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
  useEffect,
  useEffectEvent,
} from 'react';
import { Button } from '@/components/ui/button';
import { type ReviewKey, reviewShortcut } from '@/lib/campaigns/review-keys';
import type { Verb } from './approval-card';

// aria-disabled, not disabled: a disabled button drops focus to the page, and the keyboard flow
// (Tab to a button, Enter, the next message) needs it to stay on the button across messages.
const ACTION = 'phone:h-11 aria-disabled:opacity-45 aria-disabled:cursor-not-allowed';

/** «Modifica il testo», «Salta», «Approva» and their letter shortcuts A, S and E. */
export function ReviewActions({
  blocked,
  onDecide,
  onEdit,
  editRef,
}: {
  // An action running, or the editor open.
  blocked: boolean;
  onDecide: (verb: Verb) => void;
  onEdit: () => void;
  editRef: Ref<HTMLButtonElement>;
}) {
  const t = useTranslations('campaigns.approvals');
  const act = (key: ReviewKey) => {
    if (blocked) return;
    if (key === 'edit') onEdit();
    else onDecide(key);
  };
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    const key = reviewShortcut(e, document.activeElement as HTMLElement | null, blocked);
    if (!key) return;
    e.preventDefault();
    act(key);
  });
  useEffect(() => {
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // The focused button outlives its message: a held Enter or the second click of a double click
  // would decide the next one, which the owner has not read.
  const press = (key: ReviewKey) => ({
    onClick: (e: MouseEvent) => {
      if (e.detail <= 1) act(key);
    },
    onKeyDown: (e: ReactKeyboardEvent) => {
      if (e.repeat && e.key === 'Enter') e.preventDefault();
    },
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="border-line flex flex-wrap items-center gap-2 border-t pt-4">
        <Button
          ref={editRef}
          variant="ghost"
          aria-disabled={blocked}
          aria-keyshortcuts="E"
          className={ACTION}
          {...press('edit')}
        >
          <Pencil aria-hidden />
          {t('editText')}
        </Button>
        <span className="grow" />
        <Button
          variant="outline"
          aria-disabled={blocked}
          aria-keyshortcuts="S"
          className={ACTION}
          {...press('reject')}
        >
          {t('skip')}
        </Button>
        <Button
          aria-disabled={blocked}
          aria-keyshortcuts="A"
          className={ACTION}
          {...press('approve')}
        >
          {t('approve')}
        </Button>
      </div>
      <p className="text-data-sm text-muted phone:hidden">{t('shortcutsHint')}</p>
    </div>
  );
}
