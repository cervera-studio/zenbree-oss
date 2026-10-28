'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';
import { TEXT } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useBrowserTimeZone } from '@/i18n/browser-time-zone';
import type { PendingSend } from '@/lib/campaigns/api';
import { SENDER_HOLD_TONES } from '@/lib/campaigns/mailbox-state';
import { sendWhen } from '@/lib/campaigns/send-time';
import { MAX_BODY } from '@/lib/campaigns/steps';
import { cn } from '@/lib/utils';
import { whenText } from './send-when-text';
import { VariantTag } from './variant-tag';

export type Verb = 'approve' | 'reject';

export function fullName(item: PendingSend) {
  return [item.firstName, item.lastName].filter(Boolean).join(' ') || item.recipient;
}

/** «Step 1 · Email · Name Company». */
function ItemMeta({ item }: { item: PendingSend }) {
  const t = useTranslations('campaigns');
  return (
    <p className="text-muted text-small">
      {t('approvals.stepChannel', {
        step: item.step,
        channel: t(`step.channels.${item.channel}` as 'step.channels.email'),
      })}
      {' · '}
      <strong className="text-ink text-ui-strong">{fullName(item)}</strong>
      {item.companyName && ` ${item.companyName}`}
      {item.variant && ' '}
      <VariantTag variant={item.variant} />
    </p>
  );
}

export function SendLine({ item }: { item: PendingSend }) {
  const t = useTranslations('campaigns.approvals');
  const locale = useLocale();
  const timeZone = useBrowserTimeZone();
  if (!item.sendAt || !timeZone) return null;
  const linkedin = item.channel !== 'email';
  const when = sendWhen(new Date(item.sendAt), new Date(), timeZone, locale);
  const from = linkedin
    ? item.sender?.name
      ? t('fromLinkedinNamed', { name: item.sender.name })
      : t('fromLinkedin')
    : (item.sender?.address ?? item.sender?.name ?? '');
  const text = whenText(t, when, linkedin);
  return (
    <p className="text-muted text-small">
      {from ? t('sends', { when: text, from }) : t('sendsNoSender', { when: text })}
    </p>
  );
}

export function HeldLine({ item }: { item: PendingSend }) {
  const t = useTranslations('campaigns.senderHold');
  if (!item.senderHold) return null;
  return (
    <p className={cn(TEXT[SENDER_HOLD_TONES[item.senderHold]], 'text-small')}>
      {t(`reasons.${item.senderHold}`, { sender: item.sender?.address ?? '' })}
    </p>
  );
}

export function EditFields({
  item,
  onSave,
  onDone,
  autoFocus = false,
}: {
  item: PendingSend;
  onSave: (subject: string, body: string) => void;
  onDone: () => void;
  // The approval screen opens it inline (#864): the cursor goes to the first field.
  autoFocus?: boolean;
}) {
  const t = useTranslations('campaigns.approvals');
  const tb = useTranslations('campaignBuilder.steps');
  const tn = useTranslations('campaigns.step');
  const [subject, setSubject] = useState(item.subject);
  const [body, setBody] = useState(item.body ?? '');
  const email = item.channel === 'email';
  const invite = item.channel === 'linkedin_connect';
  const max = MAX_BODY[item.channel as keyof typeof MAX_BODY] ?? MAX_BODY.email;
  return (
    <div className="flex flex-col gap-2">
      {email && (
        <Input
          aria-label={t('subject')}
          value={subject}
          maxLength={300}
          onChange={(e) => setSubject(e.target.value)}
          className="phone:h-11"
          autoFocus={autoFocus}
        />
      )}
      <Textarea
        aria-label={invite ? tn('note') : t('body')}
        value={body}
        rows={email ? 8 : 4}
        maxLength={max}
        onChange={(e) => setBody(e.target.value)}
        autoFocus={autoFocus && !email}
      />
      {!email && (
        <p className="text-muted text-data-sm text-right font-mono tabular-nums">
          {tb('counter', { count: body.length, max })}
        </p>
      )}
      <div className="flex gap-2 phone:[&_button]:h-11 phone:[&_button]:flex-1">
        <Button
          size="sm"
          disabled={(email && !subject.trim()) || (!invite && !body.trim())}
          onClick={() => {
            onSave(subject, body);
            onDone();
          }}
        >
          {t('save')}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          {t('cancel')}
        </Button>
      </div>
    </div>
  );
}

export function PendingCard({
  item,
  pending,
  onDecide,
  onSave,
}: {
  item: PendingSend;
  pending: boolean;
  onDecide: (verb: Verb) => void;
  onSave: (subject: string, body: string) => void;
}) {
  const t = useTranslations('campaigns.approvals');
  const [editing, setEditing] = useState(false);
  return (
    <article className="border-line bg-raised text-ui flex h-full flex-col gap-2.5 rounded-lg border p-3.5">
      <ItemMeta item={item} />
      {editing ? (
        <EditFields item={item} onSave={onSave} onDone={() => setEditing(false)} />
      ) : (
        <>
          {item.subject && <p className="text-ui-strong text-ink">{item.subject}</p>}
          <p className="text-ink line-clamp-4 grow whitespace-pre-line">{item.body}</p>
          <SendLine item={item} />
          <HeldLine item={item} />
          <div className="border-line flex flex-wrap items-center gap-2 border-t pt-3">
            <Button size="sm" disabled={pending} onClick={() => onDecide('approve')}>
              {t('approve')}
            </Button>
            <Button size="sm" variant="outline" disabled={pending} onClick={() => setEditing(true)}>
              {t('edit')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              disabled={pending}
              onClick={() => onDecide('reject')}
            >
              {t('reject')}
            </Button>
          </div>
        </>
      )}
    </article>
  );
}
