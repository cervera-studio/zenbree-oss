import Link from 'next/link';
import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { ApiError, Empty } from '@/components/admin/api-error';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { getChatSessions } from '@/lib/admin/chats';
import { formatDate } from '@/lib/admin/format';

export async function ChatsTab({
  sessions,
}: {
  sessions: Awaited<ReturnType<typeof getChatSessions>>;
}) {
  const [t, locale, timeZone, format] = [
    await getTranslations('admin'),
    await getLocale(),
    await getTimeZone(),
    await getFormatter(),
  ];
  if (!sessions.success) return <ApiError error={sessions.error} />;
  if (sessions.data.items.length === 0) return <Empty />;
  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          <TableHead>{t('userDetail.sessionTitle')}</TableHead>
          <TableHead>{t('transcript.outcome')}</TableHead>
          <TableHead num>{t('userDetail.credits')}</TableHead>
          <TableHead num>{t('userDetail.messages')}</TableHead>
          <TableHead>{t('userDetail.updatedAt')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.data.items.map((s) => (
          <TableRow key={s.id}>
            <TableCell>
              <Link
                href={`/admin/chat-sessions/${s.id}`}
                className="text-accent-ink text-ui-strong hover:underline"
              >
                {s.title || t('userDetail.untitled')}
              </Link>
            </TableCell>
            <TableCell>
              {s.outcome === 'results' ? (
                t('userDetail.outcomeResults', { people: s.people, companies: s.companies })
              ) : s.outcome ? (
                <Badge tone={s.outcome === 'stuck' ? 'warning' : 'neutral'}>
                  {t(s.outcome === 'stuck' ? 'userDetail.outcomeStuck' : 'userDetail.outcomeNone')}
                </Badge>
              ) : (
                '—'
              )}
            </TableCell>
            <TableCell num>{format.number(s.creditsSpent, 'credits')}</TableCell>
            <TableCell num>{format.number(s.messageCount, 'credits')}</TableCell>
            <TableCell>{formatDate(s.updatedAt, { locale, timeZone })}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
