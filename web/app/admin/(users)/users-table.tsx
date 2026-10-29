import { useFormatter, useLocale, useTimeZone, useTranslations } from 'next-intl';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { UserOverview } from '@/lib/admin/api';
import { type ListUser, USER_COLUMNS } from './users-columns';

export function UsersTable({
  users,
  extra,
}: {
  users: readonly ListUser[];
  extra: ReadonlyMap<string, UserOverview>;
}) {
  const t = useTranslations('admin');
  const format = useFormatter();
  const reader = { locale: useLocale(), timeZone: useTimeZone() ?? 'UTC' };
  const now = new Date();
  const topUpsOnly = t('userList.topUpsOnly');

  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          {USER_COLUMNS.map((c) => (
            <TableHead key={c.key} num={c.num} className={c.width}>
              {c.srOnly ? <span className="sr-only">{c.header(t)}</span> : c.header(t)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {users.map((user) => (
          <TableRow key={user.id}>
            {USER_COLUMNS.map((c) => (
              <TableCell key={c.key} num={c.num}>
                {c.cell({ user, extra: extra.get(user.id), format, reader, now, topUpsOnly })}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
