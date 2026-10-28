import Link from 'next/link';
import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { getInvoices } from '@/lib/billing/api';
import { formatInvoiceAmount } from '@/lib/billing/schemas';
import { portalAction } from './actions';
import { BillingBlock } from './billing-block';

export async function InvoicesSection({ cursor, offset }: { cursor?: string; offset: number }) {
  const [t, format, locale, invoices] = await Promise.all([
    getTranslations('billing.invoices'),
    getFormatter(),
    getLocale(),
    getInvoices(cursor),
  ]);
  return (
    <BillingBlock
      title={t('title')}
      action={
        invoices.success &&
        invoices.data.portalAvailable && (
          <form action={portalAction}>
            <Button type="submit" variant="link" className="h-auto p-0">
              {t('manage')}
            </Button>
          </form>
        )
      }
    >
      <p className="text-muted text-small">{t('description')}</p>
      {!invoices.success ? (
        <p className="text-muted text-ui">{t('error')}</p>
      ) : invoices.data.items.length === 0 ? (
        <p className="text-muted text-ui">{t('empty')}</p>
      ) : (
        <StackedTable frame>
          <TableHeader>
            <TableRow>
              {(['date', 'number', 'status', 'net', 'tax', 'total', 'documents'] as const).map(
                (column) => (
                  <TableHead
                    key={column}
                    scope="col"
                    num={column === 'net' || column === 'tax' || column === 'total'}
                  >
                    {t(column)}
                  </TableHead>
                ),
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {invoices.data.items.map((invoice) => (
              <TableRow key={invoice.id}>
                <TableCell>
                  <CellLabel>{t('date')}</CellLabel>
                  {format.dateTime(new Date(invoice.createdAt), { dateStyle: 'medium' })}
                </TableCell>
                <TableCell>
                  <CellLabel>{t('number')}</CellLabel>
                  {invoice.number ?? '—'}
                </TableCell>
                <TableCell>
                  <CellLabel>{t('status')}</CellLabel>
                  <Badge tone={invoice.status === 'paid' ? 'positive' : 'neutral'}>
                    {invoice.status ? t(`statuses.${invoice.status}`) : '—'}
                  </Badge>
                </TableCell>
                <TableCell num>
                  <CellLabel>{t('net')}</CellLabel>
                  {invoice.netAmount === null
                    ? '—'
                    : formatInvoiceAmount(invoice.netAmount, invoice.currency, locale)}
                </TableCell>
                <TableCell num>
                  <CellLabel>{t('tax')}</CellLabel>
                  {invoice.taxAmount === null
                    ? '—'
                    : formatInvoiceAmount(invoice.taxAmount, invoice.currency, locale)}
                </TableCell>
                <TableCell num>
                  <CellLabel>{t('total')}</CellLabel>
                  {formatInvoiceAmount(invoice.totalAmount, invoice.currency, locale)}
                </TableCell>
                <TableCell>
                  <CellLabel>{t('documents')}</CellLabel>
                  <div className="flex flex-wrap gap-3">
                    {invoice.pdfUrl && (
                      <a
                        href={invoice.pdfUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent-ink underline"
                        aria-label={t('pdfLabel', { number: invoice.number ?? invoice.id })}
                      >
                        {t('pdf')}
                      </a>
                    )}
                    {invoice.hostedUrl && (
                      <a
                        href={invoice.hostedUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent-ink underline"
                        aria-label={t('viewLabel', { number: invoice.number ?? invoice.id })}
                      >
                        {t('view')}
                      </a>
                    )}
                    {!invoice.pdfUrl && !invoice.hostedUrl && '—'}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </StackedTable>
      )}
      <div className="flex justify-end gap-2">
        {cursor && (
          <Button asChild variant="outline" size="sm">
            <Link href={{ pathname: '/billing', query: { offset } }}>{t('newest')}</Link>
          </Button>
        )}
        {invoices.success && invoices.data.nextCursor && (
          <Button asChild variant="outline" size="sm">
            <Link
              href={{
                pathname: '/billing',
                query: { offset, invoiceCursor: invoices.data.nextCursor },
              }}
            >
              {t('older')}
            </Link>
          </Button>
        )}
      </div>
    </BillingBlock>
  );
}
