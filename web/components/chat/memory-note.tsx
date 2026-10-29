import Link from 'next/link';
import { useTranslations } from 'next-intl';

export function MemoryNote({ notes }: { notes: string[] }) {
  const t = useTranslations('chat.question');
  return (
    <p className="text-muted px-1 text-xs leading-normal">
      {t('memory', { text: notes.join(' · ') })}{' '}
      <Link href="/settings/memory" className="text-accent-ink font-semibold">
        {t('memoryLink')}
      </Link>
    </p>
  );
}
