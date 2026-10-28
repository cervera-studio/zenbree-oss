'use client';

import { Loader2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Webhook } from '@/lib/webhooks/schemas';
import { eventLabel } from './labels';

const DAY = { day: 'numeric', month: 'short', year: 'numeric' } as const;

export function WebhookTable({
  webhooks,
  busy,
  testing,
  historyOf,
  onTest,
  onHistory,
  onDelete,
}: {
  webhooks: Webhook[];
  busy: boolean;
  testing: string | null;
  historyOf: string | null;
  onTest: (webhook: Webhook) => void;
  onHistory: (webhook: Webhook) => void;
  onDelete: (webhook: Webhook) => void;
}) {
  const t = useTranslations('webhooks');
  const format = useFormatter();
  return (
    <StackedTable frame>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t('name')}</TableHead>
          <TableHead scope="col">{t('receiver')}</TableHead>
          <TableHead scope="col">{t('eventsColumn')}</TableHead>
          <TableHead scope="col">{t('created')}</TableHead>
          <TableHead scope="col">{t('status')}</TableHead>
          <TableHead scope="col">
            <span className="sr-only">{t('actions')}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {webhooks.map((webhook) => (
          <TableRow key={webhook.id}>
            <TableCell className="text-ui-strong text-ink phone:order-1">
              <CellLabel>{t('name')}</CellLabel>
              {webhook.label}
            </TableCell>
            <TableCell className="text-data font-mono text-muted phone:order-2 phone:col-span-2">
              <CellLabel visible>{t('receiver')}</CellLabel>
              <span className="phone:block phone:truncate">{webhook.host}</span>
            </TableCell>
            <TableCell className="phone:order-3 phone:col-span-2">
              <CellLabel visible>{t('eventsColumn')}</CellLabel>
              <span className="flex flex-wrap gap-1 phone:mt-1">
                {webhook.eventNames.map((name) => (
                  <Badge key={name} tone="neutral">
                    {eventLabel(t, name)}
                  </Badge>
                ))}
              </span>
            </TableCell>
            <TableCell className="text-data font-mono text-soft phone:order-4 phone:col-span-2">
              <CellLabel visible>{t('created')}</CellLabel>
              <time dateTime={webhook.createdAt}>
                {format.dateTime(new Date(webhook.createdAt), DAY)}
              </time>
            </TableCell>
            <TableCell className="phone:order-1">
              <CellLabel>{t('status')}</CellLabel>
              <Badge dot tone={webhook.goneAt ? 'warning' : 'positive'}>
                {webhook.goneAt ? t('revokedByReceiver') : t('active')}
              </Badge>
            </TableCell>
            <TableCell className="phone:order-6 phone:col-span-2">
              <span className="flex flex-wrap justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="phone:h-11"
                  disabled={busy || webhook.goneAt !== null}
                  aria-label={t('testLabel', { label: webhook.label })}
                  onClick={() => onTest(webhook)}
                >
                  {testing === webhook.id && <Loader2 className="animate-spin" />}
                  {t('test')}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="phone:h-11"
                  aria-expanded={historyOf === webhook.id}
                  aria-label={t('historyLabel', { label: webhook.label })}
                  onClick={() => onHistory(webhook)}
                >
                  {t('history')}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="text-negative border-negative phone:h-11"
                  disabled={busy}
                  aria-label={t('deleteLabel', { label: webhook.label })}
                  onClick={() => onDelete(webhook)}
                >
                  {t('delete')}
                </Button>
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </StackedTable>
  );
}
