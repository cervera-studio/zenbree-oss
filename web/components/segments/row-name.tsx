import { useTranslations } from 'next-intl';
import type { MemberRow } from '@/lib/segments/api';

const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null);

function rowTitle(row: MemberRow | null): string | null {
  if (!row) return null;
  const c = row.cells;
  const full = [text(c.first_name), text(c.last_name)].filter(Boolean).join(' ');
  return (
    text(c.full_name) ??
    text(c.name) ??
    (full || null) ??
    text(c.email) ??
    text(c.domain) ??
    text(c.linkedin_url)
  );
}

export function RowName({ row }: { row: MemberRow | null }) {
  const t = useTranslations('segments');
  const c = row?.cells ?? {};
  const detail = [text(c.title), text(c.company_name), text(c.industry)]
    .filter(Boolean)
    .join(' · ');
  return (
    <span className="flex min-w-0 flex-col">
      <span className="text-ink text-ui-strong truncate">{rowTitle(row) ?? t('noName')}</span>
      {detail && <span className="text-soft text-small truncate">{detail}</span>}
    </span>
  );
}
