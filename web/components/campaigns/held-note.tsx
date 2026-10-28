import Link from 'next/link';
import { useTranslations } from 'next-intl';

/** Mounted empty from the start, so a screen reader hears the note when an approval comes back held. */
export function HeldNote({ held, peopleHref }: { held: number; peopleHref?: string }) {
  const t = useTranslations('campaigns.approvals');
  return (
    <div role="status">
      {held > 0 && (
        <p className="bg-warning-tint text-ui rounded-lg p-3.5">
          {t('held', { count: held })}
          {peopleHref && (
            <>
              {' '}
              <Link href={peopleHref} className="text-accent-ink text-ui-strong hover:underline">
                {t('heldLink')}
              </Link>
            </>
          )}
        </p>
      )}
    </div>
  );
}
