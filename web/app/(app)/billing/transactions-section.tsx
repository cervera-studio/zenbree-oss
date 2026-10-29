import Link from 'next/link';
import { getFormatter, getTranslations } from 'next-intl/server';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { getTransactions, TRANSACTIONS_PAGE } from '@/lib/billing/api';
import { signedAmount } from '@/lib/billing/schemas';
import { TX_TONES, txKind, txType } from '@/lib/billing/transaction-types';
import { cn } from '@/lib/utils';
import { BillingBlock } from './billing-block';
import { TransactionLink } from './transaction-link';

export async function TransactionsSection({
  offset,
  invoiceCursor,
}: {
  offset: number;
  invoiceCursor?: string;
}) {
  const [t, format, txs] = await Promise.all([
    getTranslations('billing'),
    getFormatter(),
    getTransactions(offset),
  ]);

  const describe = (
    tx: Parameters<typeof txKind>[0] & { description: string | null; toolName: string | null },
  ) => {
    const kind = txKind(tx);
    return kind ? t(`descriptions.${kind}`) : (tx.description ?? tx.toolName ?? '—');
  };

  return (
    <BillingBlock title={t('transactions.title')}>
      {!txs.success ? (
        <p className="text-muted text-ui">{t('transactions.error')}</p>
      ) : txs.data.items.length === 0 ? (
        <p className="text-muted text-ui">{t('transactions.empty')}</p>
      ) : (
        <>
          <StackedTable frame>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">{t('transactions.date')}</TableHead>
                <TableHead scope="col">{t('transactions.type')}</TableHead>
                <TableHead scope="col">{t('transactions.description')}</TableHead>
                <TableHead scope="col" num>
                  {t('transactions.amount')}
                </TableHead>
                <TableHead scope="col" num>
                  {t('transactions.balanceAfter')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {txs.data.items.map((tx) => {
                const amount = signedAmount(tx);
                const type = txType(tx);
                const date = new Date(tx.createdAt);
                return (
                  <TableRow key={tx.id}>
                    <TableCell className="text-data-sm font-mono text-muted whitespace-nowrap phone:order-4">
                      <CellLabel>{t('transactions.date')}</CellLabel>
                      {Number.isFinite(date.getTime())
                        ? format.dateTime(date, { dateStyle: 'medium', timeStyle: 'short' })
                        : '—'}
                    </TableCell>
                    <TableCell className="phone:order-1">
                      <CellLabel>{t('transactions.type')}</CellLabel>
                      <Tag tone={type ? TX_TONES[type] : 'neutral'}>
                        {type ? t(`types.${type}`) : tx.type}
                      </Tag>
                    </TableCell>
                    <TableCell className="text-ui whitespace-normal phone:order-3 phone:col-span-2">
                      <CellLabel>{t('transactions.description')}</CellLabel>
                      {describe(tx)}
                      <TransactionLink tx={tx} />
                    </TableCell>
                    <TableCell num className={cn('phone:order-2', amount > 0 && 'text-positive')}>
                      <CellLabel>{t('transactions.amount')}</CellLabel>
                      {amount > 0 ? '+' : ''}
                      {format.number(amount, 'credits')}
                    </TableCell>
                    <TableCell num className="text-muted phone:order-5">
                      <CellLabel visible>{t('transactions.balanceAfter')}</CellLabel>
                      {tx.balanceAfter == null ? '—' : format.number(tx.balanceAfter, 'credits')}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </StackedTable>
          <div className="flex justify-end gap-2">
            {offset > 0 && (
              <Button asChild variant="outline" size="sm">
                <Link
                  href={{
                    pathname: '/billing',
                    query: {
                      offset: Math.max(0, offset - TRANSACTIONS_PAGE),
                      ...(invoiceCursor && { invoiceCursor }),
                    },
                  }}
                >
                  {t('transactions.newer')}
                </Link>
              </Button>
            )}
            {txs.data.nextOffset != null && (
              <Button asChild variant="outline" size="sm">
                <Link
                  href={{
                    pathname: '/billing',
                    query: { offset: txs.data.nextOffset, ...(invoiceCursor && { invoiceCursor }) },
                  }}
                >
                  {t('transactions.older')}
                </Link>
              </Button>
            )}
          </div>
        </>
      )}
    </BillingBlock>
  );
}
