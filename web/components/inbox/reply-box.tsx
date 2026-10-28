'use client';

import { RefreshCw, Send, Sparkles } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId, useRef } from 'react';
import { sendReplyAction } from '@/app/(app)/inbox/actions';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';
import { cn } from '@/lib/utils';
import { useDraftText } from './use-draft-text';

// A send refused for one of these is fixed in Integrations. A new tab keeps this one, with the box and
// its text, to send once the account is back.
const RECONNECT = new Set(['INBOX.MAILBOX_ERROR', 'INBOX.NO_MAILBOX', 'INBOX.NO_LINKEDIN']);

/** The answer box (#187): the agent's draft, editable, sent from the mailbox (or LinkedIn) named above it. */
export function ReplyBox({
  id,
  draft,
  from,
  canDraft,
}: {
  id: string;
  draft: string | null;
  from: string;
  canDraft: boolean;
}) {
  const t = useTranslations('inbox.reply');
  const fieldId = useId();
  const errorText = useErrorText();
  const box = useDraftText(id, draft);
  const send = useAction();
  // `pending` reaches the buttons a render late: a double click would start two calls.
  const busy = useRef(false);
  const pending = send.pending || box.pending;

  async function exclusive(start: () => Promise<void>) {
    if (busy.current) return;
    busy.current = true;
    send.reset();
    box.clearError();
    await start().finally(() => {
      busy.current = false;
    });
  }

  const error = box.error
    ? errorText(box.error, t('regenerateError'))
    : send.error && errorText(send.error, t('sendError'));

  return (
    <form
      id="reply"
      className={cn(
        'flex shrink-0 flex-col gap-2 rounded-lg border px-3.5 py-3',
        box.agent ? 'border-accent-soft bg-accent-tint' : 'border-line bg-ground',
      )}
      onSubmit={(e) => {
        e.preventDefault();
        const body = box.text.trim();
        if (body) exclusive(() => send.run(() => sendReplyAction(id, body)));
      }}
    >
      <label
        htmlFor={fieldId}
        className={cn(
          'text-small flex items-center gap-1.5 break-words',
          box.agent ? 'text-accent-ink' : 'text-muted',
        )}
      >
        {box.agent && <Sparkles aria-hidden="true" className="size-3.5 shrink-0" />}
        {t(box.agent ? 'draftFrom' : 'answerFrom', { from })}
      </label>
      <Textarea
        id={fieldId}
        name="body"
        rows={5}
        maxLength={4000}
        required
        value={box.text}
        onChange={(e) => box.edit(e.target.value)}
        readOnly={box.pending}
        aria-busy={box.pending}
        placeholder={t('placeholder')}
        className={cn(
          'border-line bg-ground text-body resize-y rounded-md px-3 py-2.5 shadow-none md:text-ui',
          box.agent && 'text-accent-ink',
          box.pending && 'opacity-60',
        )}
      />
      {error && (
        <p role="alert" className="text-negative text-ui">
          {error}
          {send.error && RECONNECT.has(send.error.code) && (
            <>
              {' '}
              <Link
                href="/settings/integrations"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent-ink underline"
              >
                {t('openIntegrations')}
              </Link>
            </>
          )}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {box.canRestore && (
          <Button
            type="button"
            variant="link"
            size="sm"
            disabled={pending}
            className="mr-auto"
            onClick={box.restore}
          >
            {t('restore')}
          </Button>
        )}
        {canDraft && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={pending}
            onClick={() => exclusive(box.regenerate)}
          >
            <RefreshCw aria-hidden="true" className={cn(box.pending && 'animate-spin')} />
            {box.pending ? t('regenerating') : t(box.agent ? 'regenerate' : 'writeDraft')}
          </Button>
        )}
        <Button type="submit" size="sm" disabled={pending}>
          <Send aria-hidden="true" />
          {send.pending ? t('sending') : t('send')}
        </Button>
      </div>
    </form>
  );
}
