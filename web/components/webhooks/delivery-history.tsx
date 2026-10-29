'use client';

import { Loader2, RefreshCw } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Webhook, WebhookDelivery } from '@/lib/webhooks/schemas';
import { eventLabel } from './labels';

const WHEN = {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
} as const;

export type History = { webhook: Webhook; deliveries: WebhookDelivery[] | null; failed: boolean };

export function DeliveryHistory({
  history,
  loading,
  onRefresh,
}: {
  history: History;
  loading: boolean;
  onRefresh: () => void;
}) {
  const t = useTranslations('webhooks');
  const format = useFormatter();
  const { webhook, deliveries, failed } = history;
  return (
    <section aria-labelledby="webhook-history-title" className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h3 id="webhook-history-title" className="text-ui-strong grow">
          {t('historyTitle', { label: webhook.label })}
        </h3>
        <Button
          type="button"
          variant="outline"
          size="xs"
          className="phone:h-11"
          disabled={loading}
          onClick={onRefresh}
        >
          {loading ? <Loader2 className="animate-spin" /> : <RefreshCw aria-hidden />}
          {t('refresh')}
        </Button>
      </div>
      {webhook.goneAt && <p className="text-small text-warning">{t('revokedHelp')}</p>}
      <p className="text-small text-muted">{t('historyHelp')}</p>
      {failed ? (
        <p role="alert" className="text-muted text-ui">
          {t('historyError')}
        </p>
      ) : deliveries === null ? null : deliveries.length === 0 ? (
        <p className="text-muted text-ui">{t('historyEmpty')}</p>
      ) : (
        <StackedTable frame>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('when')}</TableHead>
              <TableHead scope="col">{t('event')}</TableHead>
              <TableHead scope="col">{t('eventId')}</TableHead>
              <TableHead scope="col">{t('attempt')}</TableHead>
              <TableHead scope="col">{t('result')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {deliveries.map((delivery) => (
              <TableRow key={delivery.id}>
                <TableCell className="text-data font-mono text-soft phone:order-3 phone:col-span-2">
                  <CellLabel visible>{t('when')}</CellLabel>
                  <time dateTime={delivery.attemptedAt}>
                    {format.dateTime(new Date(delivery.attemptedAt), WHEN)}
                  </time>
                </TableCell>
                <TableCell className="text-ui-strong text-ink phone:order-2 phone:col-span-2">
                  <CellLabel>{t('event')}</CellLabel>
                  {eventLabel(t, delivery.eventType)}{' '}
                  {delivery.isTest && <Badge tone="accent">{t('testBadge')}</Badge>}
                </TableCell>
                <TableCell className="text-data font-mono text-muted phone:order-4 phone:col-span-2">
                  <CellLabel visible>{t('eventId')}</CellLabel>
                  <span className="phone:block phone:truncate">{delivery.eventId}</span>
                </TableCell>
                <TableCell className="text-data font-mono text-soft phone:order-5 phone:col-span-2">
                  <CellLabel visible>{t('attempt')}</CellLabel>
                  {delivery.attempt}
                </TableCell>
                <TableCell className="phone:order-1 phone:col-span-2">
                  <CellLabel>{t('result')}</CellLabel>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <Badge dot tone={delivery.category ? 'negative' : 'positive'}>
                      {delivery.category ? t(`categories.${delivery.category}`) : t('delivered')}
                    </Badge>
                    <span className="text-data font-mono text-soft">
                      {delivery.statusCode ?? t('noAnswer')}
                    </span>
                    {delivery.willRetry && (
                      <span className="text-small text-muted">{t('retryQueued')}</span>
                    )}
                  </span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </StackedTable>
      )}
    </section>
  );
}
