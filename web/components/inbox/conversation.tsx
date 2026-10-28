import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Avatar } from '@/components/ui/avatar';
import { LABELS } from '@/lib/inbox/api';
import { cn } from '@/lib/utils';
import { Answer, Bubble, SequenceNotice } from './conversation-parts';
import { LabelSelect } from './label-select';
import { StateTag } from './state-tag';
import { SequenceStopped, Unsubscribed, UnverifiedNotice } from './thread-notices';
import { type Message, personName, type ThreadData } from './types';

async function ConversationHead({ message: m }: { message: Message }) {
  const t = await getTranslations('inbox');
  const name = personName(m);
  const roleAndCompany = [m.targetRole, m.targetCompanyName].filter(Boolean).join(' · ');
  const who = roleAndCompany || m.fromAddress;
  return (
    <header className="border-line flex shrink-0 flex-wrap items-center gap-3 border-b px-5 py-3.5 phone:px-4">
      <Avatar user={{ name, email: m.fromAddress }} size="md" tone="accent" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <h2 className="text-feature truncate">{name}</h2>
        <p className="text-small text-muted break-words">
          <span
            className={cn('inline-block max-w-full align-bottom', !roleAndCompany && 'truncate')}
          >
            {who}
          </span>
          {m.campaignId && (
            <>
              {' '}
              {/* inline-block: the separator and the name move to the next line together, never apart */}
              <span className="inline-block">
                {'· '}
                <Link
                  href={`/campaigns/${m.campaignId}`}
                  className="text-accent-ink hover:underline"
                >
                  {m.campaignName || t('campaign')}
                </Link>
              </span>
            </>
          )}
        </p>
      </div>
      {m.kind === 'reply' ? (
        <LabelSelect id={m.id} label={m.label} labels={LABELS} auto={m.labelSource === 'llm'} />
      ) : (
        <StateTag message={m} />
      )}
    </header>
  );
}

/** The open thread: who wrote, the steps that reached them, their message, our answers, the box. */
export async function Conversation({
  thread,
  back,
  className,
}: {
  thread: ThreadData;
  back: string;
  className?: string;
}) {
  const t = await getTranslations('inbox');
  const m = thread.message;
  const name = personName(m);
  const first = m.targetFirstName || name;
  return (
    <article
      aria-label={t('thread', { name })}
      className={cn('flex min-h-0 min-w-0 flex-1 flex-col', className)}
    >
      <Link
        href={back}
        className="text-muted text-ui flex min-h-11 shrink-0 items-center px-4 desktop:hidden"
      >
        {t('backToList')}
      </Link>
      <ConversationHead message={m} />
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 phone:px-4">
        {thread.replyBlocked === 'INBOX.UNSUBSCRIBED' && <Unsubscribed name={first} />}
        <UnverifiedNotice authenticated={m.senderAuthenticated} />
        <SequenceNotice thread={thread} first={first} />
        {thread.sent.map((s) => (
          <Bubble
            key={`step-${s.step}`}
            ours
            who={`${t('you')} · ${t('step', { n: s.step })}`}
            at={s.deliveredAt}
            subject={s.subject}
            body={s.body ?? ''}
          />
        ))}
        <Bubble who={first} at={m.receivedAt} subject={m.subject} body={m.body} />
        <SequenceStopped message={m} name={first} />
        {m.kind === 'auto_reply' && m.targetStatus === 'paused' && (
          <p className="text-muted text-small text-center">{t('autoReply.pausedNote')}</p>
        )}
        {thread.replies.map((r) => (
          <Bubble key={r.id} ours who={t('you')} at={r.createdAt} body={r.body} status={r.status} />
        ))}
        <Answer thread={thread} />
      </div>
    </article>
  );
}
