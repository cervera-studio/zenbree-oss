import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { PAGE_SIZE } from '@/lib/inbox/api';
import { type Clean, href } from '@/lib/inbox/filters';

const LINK = 'text-accent-ink text-ui-strong hover:underline';

export async function Pager({ filters, full }: { filters: Clean; full: boolean }) {
  const t = await getTranslations('inbox');
  if (filters.offset === 0 && !full) return null;
  return (
    <div className="border-line text-ui flex shrink-0 justify-between border-t px-4 py-3">
      {filters.offset > 0 ? (
        <Link
          href={href({ ...filters, offset: Math.max(0, filters.offset - PAGE_SIZE) })}
          className={LINK}
        >
          {t('newer')}
        </Link>
      ) : (
        <span />
      )}
      {full && (
        <Link href={href({ ...filters, offset: filters.offset + PAGE_SIZE })} className={LINK}>
          {t('older')}
        </Link>
      )}
    </div>
  );
}
