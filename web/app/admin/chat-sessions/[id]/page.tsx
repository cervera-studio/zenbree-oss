import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { AdminPage } from '@/components/admin/admin-page';
import { ApiError, Empty } from '@/components/admin/api-error';
import { Button } from '@/components/ui/button';
import { getChatSession } from '@/lib/admin/chats';
import { formatDate } from '@/lib/admin/format';
import { isUuid } from '@/lib/config/uuid';
import { cn } from '@/lib/utils';
import { MessageCard } from './message-card';
import { Summary } from './summary';

export default async function ChatTranscript({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  if (!isUuid(id)) notFound();
  // Two read-only views, in the URL so they survive a reload: every tool call open, tool calls only.
  const open = sp.open === '1';
  const toolsOnly = sp.tools === '1';
  const [t, locale, timeZone, res] = await Promise.all([
    getTranslations('admin'),
    getLocale(),
    getTimeZone(),
    getChatSession(id),
  ]);
  if (!res.success) {
    if (res.error.status === 404) notFound();
    return (
      <AdminPage crumbs={[{ label: t('users'), href: '/admin' }, { label: t('transcript.crumb') }]}>
        <ApiError
          error={res.error}
          request={`GET /v1/admin/chat-sessions/${id}`}
          retryHref={`/admin/chat-sessions/${id}`}
        />
      </AdminPage>
    );
  }
  const s = res.data;
  const href = (o: boolean, tools: boolean) => {
    const q = new URLSearchParams({ ...(o && { open: '1' }), ...(tools && { tools: '1' }) });
    return `/admin/chat-sessions/${id}${q.size ? `?${q}` : ''}`;
  };
  const shown = toolsOnly ? s.messages.filter((m) => m.toolCalls?.length) : s.messages;

  return (
    <AdminPage
      crumbs={[
        { label: t('users'), href: '/admin' },
        s.userId
          ? { label: s.email ?? s.userId, href: `/admin/users/${s.userId}` }
          : { label: t('formerMember') },
        { label: t('transcript.crumb') },
      ]}
      className="flex-row items-start gap-5 phone:flex-col phone:items-stretch"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {/* The page head sits in the left column: the side column starts level with the title. */}
        <header className="flex flex-col gap-0.5">
          <h1 className="text-title">{s.title || t('userDetail.untitled')}</h1>
          <p className="text-small text-muted">
            {t('transcript.meta', {
              n: s.messages.length,
              date: formatDate(s.createdAt, { locale, timeZone }),
            })}
          </p>
        </header>
        <nav aria-label={t('transcript.views')} className="flex flex-wrap gap-2">
          <Toggle href={href(!open, toolsOnly)} on={open}>
            {t('transcript.openAll')}
          </Toggle>
          <Toggle href={href(open, !toolsOnly)} on={toolsOnly}>
            {t('transcript.toolsOnly')}
          </Toggle>
        </nav>
        {shown.length === 0 ? (
          <Empty />
        ) : (
          <div className="border-line bg-ground flex flex-col overflow-hidden rounded-lg border">
            {shown.map((m) => (
              <MessageCard key={m.id} m={m} open={open} toolsOnly={toolsOnly} />
            ))}
          </div>
        )}
      </div>
      <Summary s={s} />
    </AdminPage>
  );
}

function Toggle({ href, on, children }: { href: string; on: boolean; children: ReactNode }) {
  return (
    <Button
      asChild
      variant="outline"
      size="sm"
      className={cn(on && 'border-accent-tint bg-accent-tint text-accent-ink')}
    >
      <Link href={href} aria-current={on ? 'true' : undefined}>
        {children}
      </Link>
    </Button>
  );
}
