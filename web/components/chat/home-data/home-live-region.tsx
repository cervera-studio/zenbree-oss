import { useLocale, useTranslations } from 'next-intl';
import type { Announced, Announcement } from '@/lib/home/live';

export function HomeLiveRegion({ announced }: { announced: readonly Announced[] }) {
  const t = useTranslations('chat.home.data.live');
  const ti = useTranslations('inbox.label');
  const tc = useTranslations('chat');
  const locale = useLocale();

  const say = (a: Announcement) => {
    if (a.kind === 'approvals') return t('approvals', { added: a.added, total: a.total });
    if (a.kind === 'reply') {
      return t('reply', {
        name: a.name ?? 'none',
        intent: a.intent ? ti(a.intent).toLocaleLowerCase(locale) : 'none',
        count: a.count,
      });
    }
    return t(a.entity === 'company' ? 'finishedCompanies' : 'finishedPeople', {
      title: a.title || tc('untitled'),
      rows: a.rows,
    });
  };

  return (
    <div className="sr-only" aria-live="polite">
      {announced.map((a) => (
        <p key={a.id}>{say(a)}</p>
      ))}
    </div>
  );
}
