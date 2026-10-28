import { CheckIcon } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import { Tag } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

// Sample rows for the illustration; names, not copy, so they stay out of the message files.
const SAMPLE_ROWS = [
  { name: 'Giulia Ferri', company: 'Officina Nove', email: 'giulia.ferri@…' },
  { name: 'Marco Bellini', company: 'Studio Brezza', email: 'm.bellini@…' },
  { name: 'Chiara Gallo', company: 'Lanterna Digital', email: 'chiara@…' },
];

// A dark plane in both modes: the inverse plane in light mode, and `raised` in dark mode, where the
// design's light inverse plane read wrong to the owner (2026-10-15). `raised` stays apart from the
// form's `ground`, and the `ground` cards on it read as set in.
export async function AuthAside() {
  const t = await getTranslations('auth.aside');
  return (
    <aside
      aria-label={t('label')}
      className="hidden w-140 flex-none flex-col justify-between bg-inverse p-10 text-on-inverse lg:flex dark:bg-raised dark:text-ink"
    >
      <span className="text-eyebrow font-mono uppercase tabular-nums">{t('eyebrow')}</span>

      <div className="flex flex-col gap-6">
        <h2 className="text-headline">{t('title')}</h2>
        <p className="text-lead text-inverse-soft dark:text-soft">{t('lede')}</p>

        <div aria-hidden className="flex flex-col gap-2">
          <div className="text-ui max-w-90 rounded-lg border border-line bg-ground px-3 py-2.5 text-ink">
            {t('brief')}
          </div>
          <div className="flex items-center gap-2.5 rounded-lg border border-line bg-ground px-3 py-2 text-ink">
            <span className="flex size-6 items-center justify-center rounded-md bg-accent-tint text-accent-ink">
              <CheckIcon className="size-3.5" strokeWidth={2} />
            </span>
            <span className="text-ui-strong">{t('plan')}</span>
            <span className="text-data-sm font-mono text-muted tabular-nums">{t('planMeta')}</span>
          </div>
          <Table frame className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead>{t('name')}</TableHead>
                <TableHead>{t('company')}</TableHead>
                <TableHead className="w-2/5">{t('email')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {SAMPLE_ROWS.map((row) => (
                <TableRow key={row.name}>
                  <TableCell className="truncate text-ink font-bold">{row.name}</TableCell>
                  <TableCell className="truncate text-ink">{row.company}</TableCell>
                  <TableCell>
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="text-data-sm truncate font-mono text-ink">{row.email}</span>
                      <Tag tone="positive">{t('verified')}</Tag>
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <ul className="flex flex-col gap-2">
          {(['free', 'delivered', 'sourced'] as const).map((point) => (
            <li key={point} className="text-ui flex items-center gap-2.5">
              <CheckIcon aria-hidden className="size-4 shrink-0" strokeWidth={2} />
              {t(point)}
            </li>
          ))}
        </ul>
      </div>

      <span className="text-small text-inverse-soft dark:text-soft">{t('footnote')}</span>
    </aside>
  );
}
