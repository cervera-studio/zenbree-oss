import { ChevronRight } from 'lucide-react';
import { getFormatter, getTranslations } from 'next-intl/server';
import { SummaryLine } from '@/components/chat/summary-line';
import { Badge, Tag } from '@/components/ui/badge';
import type { ChatSessionDetail } from '@/lib/admin/chats';

type Message = ChatSessionDetail['messages'][number];

/** One message of the transcript: its role, time and credits, its text, and its tool calls. */
export async function MessageCard({
  m,
  open,
  toolsOnly,
}: {
  m: Message;
  open: boolean;
  toolsOnly: boolean;
}) {
  const [t, format] = await Promise.all([getTranslations('admin.transcript'), getFormatter()]);
  const user = m.role === 'user';
  const q = m.question;
  const options = q?.options?.map((o) => (typeof o === 'string' ? o : o.label)) ?? [];

  return (
    <article className="border-hair flex flex-col gap-1.5 border-b px-3 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <Tag tone={user ? 'accent' : 'neutral'}>{t(user ? 'user' : 'agent')}</Tag>
        <time dateTime={m.createdAt} className="text-data-sm text-muted flex-1 font-mono">
          {format.dateTime(new Date(m.createdAt), { timeStyle: 'medium' })}
        </time>
        {m.creditsUsed > 0 && (
          <span className="text-data-sm text-ink font-mono font-bold">
            {t('credits', { n: m.creditsUsed })}
          </span>
        )}
      </div>
      {!toolsOnly && m.content && (
        <p className="text-ui text-ink whitespace-pre-wrap">{m.content}</p>
      )}
      {/* Since #246 the ask_user question lives only here, not in `content`. */}
      {!toolsOnly && q && (q.question || options.length > 0 || q.plan) && (
        <div className="border-line-strong flex flex-col gap-1.5 rounded-md border border-dashed px-3 py-2">
          <p className="text-label text-muted uppercase">
            {q.plan ? t('planAsked') : t('questionAsked')}
          </p>
          {q.question && <p className="text-ui text-ink">{q.question}</p>}
          {q.plan?.estimatedCredits !== undefined && (
            <p className="text-ui text-ink">{t('planCredits', { n: q.plan.estimatedCredits })}</p>
          )}
          {options.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {options.map((o) => (
                <li key={o}>
                  <Tag tone="neutral">{o}</Tag>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {m.toolCalls?.map((c, i) => (
        <details key={c.id ?? i} open={open} className="group bg-raised rounded-sm">
          <summary className="text-small flex min-h-6.5 cursor-pointer list-none items-center gap-2 px-2 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              className="size-3 shrink-0 transition-transform group-open:rotate-90"
              aria-hidden
            />
            <span className="text-data-sm text-ink font-mono font-bold">
              {t('tool')}: {c.name ?? '?'}
            </span>
            <span className="min-w-0 flex-1">
              {c.summary && <SummaryLine summary={c.summary} />}
            </span>
            {c.verdict === 'failed' && <Badge tone="negative">{t('failed')}</Badge>}
            {c.vendors && c.vendors.length > 0 && (
              <span className="text-data-sm text-muted font-mono">{c.vendors.join(' → ')}</span>
            )}
            <span className="text-data-sm text-ink font-mono font-bold tabular-nums">
              {t('cr', { n: c.creditsUsed ?? 0 })}
            </span>
          </summary>
          <pre className="border-hair text-data-sm overflow-x-auto border-t px-2 py-2 font-mono whitespace-pre-wrap">
            {JSON.stringify({ input: c.input, result: c.result }, null, 2)}
          </pre>
        </details>
      ))}
    </article>
  );
}
