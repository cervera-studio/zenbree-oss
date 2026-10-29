import { X } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { ApiError, Empty } from '@/components/admin/api-error';
import { Pager, parseOffset } from '@/components/admin/pager';
import { FilterChip } from '@/components/ui/filter-chip';
import { Table, TableBody, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { getJobs } from '@/lib/admin/api';
import { isUuid } from '@/lib/config/uuid';
import { JobRow } from './job-row';
import { COLUMNS, isJobStatus, JOB_STATUS, JOB_STATUSES, type JobStatus } from './job-status';

export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const offset = parseOffset(sp.offset);
  const status = isJobStatus(sp.status) ? sp.status : undefined;
  // A non-uuid id would get a 422 from the API: drop it and show the unfiltered list.
  const user = typeof sp.user === 'string' && isUuid(sp.user) ? sp.user : undefined;
  const job = typeof sp.job === 'string' && isUuid(sp.job) ? sp.job.toLowerCase() : undefined;
  const [t, ta, res] = await Promise.all([
    getTranslations('admin.jobs'),
    getTranslations('admin'),
    getJobs(offset, { status, userId: user, jobId: job }),
  ]);
  const href = (q: { status?: JobStatus; user?: string; job?: string }) => {
    const s = new URLSearchParams(Object.entries(q).filter((e): e is [string, string] => !!e[1]));
    return s.size ? `/admin/jobs?${s}` : '/admin/jobs';
  };
  // The user's email comes with their jobs; with none, the id stands in for it.
  const userLabel = res.success && res.data.items[0]?.email;
  const chips = [
    user && {
      key: 'user',
      label: t('filteredUser', { user: userLabel || user.slice(0, 8) }),
      clear: href({ status, job }),
    },
    job && {
      key: 'job',
      label: t('filteredJob', { id: job.slice(0, 8) }),
      clear: href({ status, user }),
    },
  ].filter((c) => !!c);

  return (
    <AdminPage
      crumbs={sectionCrumbs(ta('shell.label'), ta('nav.jobs'))}
      title={t('title')}
      meta={t('subtitle')}
    >
      {!res.success ? (
        <ApiError
          error={res.error}
          request={`GET /v1/admin/jobs?limit=50&offset=${offset}${status ? `&status=${status}` : ''}${user ? `&user_id=${user}` : ''}${job ? `&job_id=${job}` : ''}`}
          retryHref={href({ status, user, job })}
        />
      ) : (
        <>
          {chips.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-ui text-muted">{t('filtered')}</span>
              {chips.map((c) => (
                <span
                  key={c.key}
                  className="bg-accent-tint text-accent-ink text-ui-strong inline-flex h-control-sm items-center gap-1 rounded-md pr-0.5 pl-2.25"
                >
                  {c.label}
                  <Link
                    href={c.clear}
                    aria-label={t('clearFilter', { filter: c.label })}
                    className="hover:bg-ground inline-flex size-5.5 items-center justify-center rounded-sm"
                  >
                    <X className="size-3.5" aria-hidden />
                  </Link>
                </span>
              ))}
            </div>
          )}
          <nav aria-label={t('filters')} className="flex flex-wrap gap-2">
            {[undefined, ...JOB_STATUSES].map((s) => {
              const meta = s && JOB_STATUS[s];
              const n = meta
                ? (res.data.counts[meta.label] ?? 0)
                : Object.values(res.data.counts).reduce((a, b) => a + b, 0);
              return (
                <FilterChip
                  key={s ?? 'all'}
                  href={href({ status: s, user, job })}
                  on={status === s}
                  count={n}
                  dot={meta?.tone}
                >
                  {meta ? t(`statuses.${meta.label}`) : t('all')}
                </FilterChip>
              );
            })}
          </nav>

          {res.data.items.length === 0 ? (
            <Empty />
          ) : (
            <Table frame className="min-w-240">
              <TableHeader>
                <TableRow>
                  {COLUMNS.map(([key, numeric]) => (
                    <TableHead key={key} scope="col" num={numeric}>
                      {t(key)}
                    </TableHead>
                  ))}
                  <TableHead scope="col">
                    <span className="sr-only">{t('details')}</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {res.data.items.map((j) => (
                  <JobRow key={j.id} job={j} open={j.id === job} />
                ))}
              </TableBody>
            </Table>
          )}

          <Pager
            path="/admin/jobs"
            params={{ status, user, job }}
            offset={offset}
            limit={50}
            hasNext={res.data.nextOffset !== null}
            note={t('footer')}
          />
        </>
      )}
    </AdminPage>
  );
}
