import Link from 'next/link';
import { useTranslations } from 'next-intl';
import type { Transaction } from '@/lib/billing/schemas';

export function TransactionLink({ tx }: { tx: Pick<Transaction, 'listId' | 'sessionId'> }) {
  const t = useTranslations('billing.transactions');
  const href = tx.listId ? `/lists/${tx.listId}` : tx.sessionId && `/?s=${tx.sessionId}`;
  if (!href) return null;
  return (
    <Link href={href} className="text-ui-strong text-accent-ink ml-2">
      {t(tx.listId ? 'openList' : 'openChat')}
    </Link>
  );
}
