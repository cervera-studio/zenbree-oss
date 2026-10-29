import { getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { SkeletonBar } from '@/components/admin/skeleton-bar';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { USER_COLUMNS } from './users-columns';

const ROWS = 10;

// In the (users) group so it covers only the users list, not the other admin pages.
export default async function AdminUsersLoading() {
  const [t, tc] = [await getTranslations('admin'), await getTranslations('common')];
  return (
    <AdminPage
      crumbs={sectionCrumbs(t('shell.label'), t('users'))}
      title={t('users')}
      meta={<SkeletonBar className="w-75 max-w-full" />}
      busy
    >
      <span className="sr-only">{tc('loading')}</span>
      <div className="flex flex-wrap items-center gap-2.5">
        <SkeletonBar className="h-control-md w-full max-w-sm desktop:w-85" />
        <SkeletonBar className="h-control-sm w-15 rounded-md" />
        <SkeletonBar className="h-control-sm w-18 rounded-md" />
        <SkeletonBar className="h-control-sm w-20 rounded-md" />
      </div>
      <Table frame>
        <TableHeader>
          <TableRow>
            {USER_COLUMNS.map((c) => (
              <TableHead key={c.key} num={c.num} className={c.width} />
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: ROWS }, (_, i) => (
            <TableRow key={i}>
              {USER_COLUMNS.map((c) => (
                <TableCell key={c.key} num={c.num}>
                  {c.skeleton}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </AdminPage>
  );
}
