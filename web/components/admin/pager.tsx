import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import { pageOffsets, pageRange } from '@/lib/admin/pager';

/** The row under a list: the range shown (or a note when the total is unknown), then
 * prev/next links that keep the other query params. */
export async function Pager({
  path,
  params,
  offset,
  limit,
  hasNext,
  shown = 0,
  total,
  note,
}: {
  path: string;
  params: Record<string, string | undefined>;
  offset: number;
  limit: number;
  hasNext: boolean;
  /** Rows on this page; with `total` the label reads "1–10 di 214". */
  shown?: number;
  total?: number;
  note?: string;
}) {
  const t = await getTranslations('admin.common');
  const href = (o: number) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
    if (o > 0) q.set('offset', String(o));
    const s = q.toString();
    return s ? `${path}?${s}` : path;
  };
  const range = total === undefined ? null : pageRange(offset, shown, total);
  const { prev, next } = pageOffsets(offset, limit, hasNext);
  const link = (label: string, o: number | null) =>
    o === null ? (
      <Button variant="outline" size="sm" disabled>
        {label}
      </Button>
    ) : (
      <Button variant="outline" size="sm" asChild>
        <Link href={href(o)}>{label}</Link>
      </Button>
    );
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-muted text-small grow">{range ? t('range', range) : note}</p>
      {link(t('prev'), prev)}
      {link(t('next'), next)}
    </div>
  );
}

export function parseOffset(v: string | string[] | undefined): number {
  const n = Number(Array.isArray(v) ? v[0] : v);
  return Number.isInteger(n) && n > 0 ? n : 0;
}
