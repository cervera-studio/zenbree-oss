import { getFormatter, getTranslations } from 'next-intl/server';
import { StatusNotice } from '@/components/settings/status-notice';
import { Badge } from '@/components/ui/badge';
import { restartDay } from '@/lib/inbox/dates';
import { cn } from '@/lib/utils';
import { ReplyBox } from './reply-box';
import { ReminderSet } from './thread-notices';
import type { ThreadData } from './types';

export async function SequenceNotice({ thread, first }: { thread: ThreadData; first: string }) {
  const [t, format] = await Promise.all([getTranslations('inbox'), getFormatter()]);
  const { message: m, reminder } = thread;
  if (m.kind === 'auto_reply') {
    const text =
      m.targetStatus === 'paused' && m.targetPausedUntil
        ? t('autoReply.paused', { name: first, ...restartDay(format, m.targetPausedUntil) })
        : t(m.targetStatus === 'active' ? 'autoReply.continues' : 'autoReply.none', {
            name: first,
          });
    return <StatusNotice tone="neutral">{text}</StatusNotice>;
  }
  if (!reminder) return null;
  return (
    <StatusNotice tone="warning">
      {reminder.surfacedAt ? (
        t('reminder.due', { name: first })
      ) : (
        <ReminderSet hasTarget={Boolean(m.targetId)} name={first} remindOn={reminder.remindOn} />
      )}
    </StatusNotice>
  );
}

const STATUS_TONE = { sending: 'accent', failed: 'negative', unknown: 'warning' } as const;

/** One message of the thread: ours (a campaign step or an answer) on the raised plane. */
export async function Bubble({
  who,
  at,
  subject,
  body,
  ours = false,
  status,
}: {
  who: string;
  at: string;
  subject?: string;
  body: string;
  ours?: boolean;
  status?: 'sending' | 'sent' | 'failed' | 'unknown';
}) {
  const [t, format] = await Promise.all([getTranslations('inbox'), getFormatter()]);
  return (
    <section
      className={cn(
        'flex flex-col gap-1.5 rounded-lg border px-3.5 py-3',
        ours ? 'bg-raised border-hair' : 'bg-ground border-line',
      )}
    >
      <p className="text-small text-muted flex items-center gap-2">
        {who} · {format.relativeTime(new Date(at), new Date())}
        {status && status !== 'sent' && (
          <Badge tone={STATUS_TONE[status]}>{t(`reply.status.${status}`)}</Badge>
        )}
      </p>
      {subject && <h3 className="text-ui-strong">{subject}</h3>}
      <p className="text-ui break-words whitespace-pre-line">{body}</p>
    </section>
  );
}

/** The bottom of the thread: the answer box, or why there is none. */
export async function Answer({ thread }: { thread: ThreadData }) {
  const t = await getTranslations('inbox');
  const blocked = thread.replyBlocked?.replace(/^INBOX\./, '');
  // An opt-out says so at the top of the thread instead (`Unsubscribed`).
  if (blocked === 'UNSUBSCRIBED') return null;
  if (blocked || !thread.replyFrom) {
    return (
      <p className="border-line-strong text-muted text-ui rounded-lg border border-dashed p-4">
        {thread.replyHold
          ? t(`reply.held.${thread.replyHold}`)
          : t(`reply.blocked.${blocked ?? 'NO_MAILBOX'}` as 'reply.blocked.NO_MAILBOX')}
      </p>
    );
  }
  const from = thread.replyFrom === 'linkedin' ? t('channel.linkedin') : thread.replyFrom;
  const m = thread.message;
  // The API's rule for a draft (docs/contracts-m1.md, inbox): otherwise «Rigenera» could only fail.
  const canDraft =
    m.kind === 'reply' &&
    m.label !== null &&
    m.label !== 'not_interested' &&
    m.senderAuthenticated !== false;
  const sentAnswers = thread.replies.filter((r) => r.status === 'sent').length;
  return (
    <ReplyBox
      key={`${m.id}:${sentAnswers}`}
      id={m.id}
      draft={thread.draft}
      from={from}
      canDraft={canDraft}
    />
  );
}
