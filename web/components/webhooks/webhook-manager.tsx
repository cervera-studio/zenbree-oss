'use client';

import { useTranslations } from 'next-intl';
import { useState, useTransition } from 'react';
import {
  createWebhookAction,
  deleteWebhookAction,
  getDeliveriesAction,
  testWebhookAction,
} from '@/app/(app)/settings/webhooks/actions';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import type { Webhook } from '@/lib/webhooks/schemas';
import { DeliveryHistory, type History } from './delivery-history';
import { SecretReveal } from './secret-reveal';
import { WebhookForm, type WebhookInput } from './webhook-form';
import { WebhookTable } from './webhook-table';

// The API's cap (MAX_ENDPOINTS in api/app/integrations/webhooks.py); it answers 409 past it.
const MAX_WEBHOOKS = 10;

export function WebhookManager({
  initialWebhooks,
  events,
  loadFailed,
}: {
  initialWebhooks: Webhook[];
  events: string[];
  loadFailed: boolean;
}) {
  const t = useTranslations('webhooks');
  const errorText = useErrorText();
  const [webhooks, setWebhooks] = useState(initialWebhooks);
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState<Webhook | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [pending, startTransition] = useTransition();

  async function loadHistory(webhook: Webhook) {
    setLoadingHistory(true);
    const result = await getDeliveriesAction(webhook.id);
    setHistory((current) =>
      current && current.webhook.id !== webhook.id
        ? current // the admin opened another endpoint while this one loaded
        : {
            webhook,
            deliveries: result.success ? result.data.deliveries : null,
            failed: !result.success,
          },
    );
    setLoadingHistory(false);
  }

  function toggleHistory(webhook: Webhook) {
    if (history?.webhook.id === webhook.id) {
      setHistory(null);
      return;
    }
    setHistory({ webhook, deliveries: null, failed: false });
    void loadHistory(webhook);
  }

  function create(input: WebhookInput) {
    setError('');
    return new Promise<boolean>((resolve) => {
      startTransition(async () => {
        const result = await createWebhookAction(input);
        if (!result.success) {
          setError(errorText(result.error, t('actionError')));
          resolve(false);
          return;
        }
        const { secret: created, ...webhook } = result.data;
        setSecret(created);
        setWebhooks((current) => [...current, webhook]);
        resolve(true);
      });
    });
  }

  async function test(webhook: Webhook) {
    setError('');
    setTesting(webhook.id);
    const result = await testWebhookAction(webhook.id);
    setTesting(null);
    if (!result.success) {
      setError(errorText(result.error, t('actionError')));
      return;
    }
    const { status } = result.data;
    if (status === 410) {
      // 410 Gone: the API stopped the endpoint and kept it listed, with its history.
      const goneAt = new Date().toISOString();
      setWebhooks((current) => current.map((w) => (w.id === webhook.id ? { ...w, goneAt } : w)));
      toast.error(t('testGone', { label: webhook.label }));
      if (history?.webhook.id === webhook.id) void loadHistory({ ...webhook, goneAt });
      return;
    }
    const message =
      status === null ? t('testNoAnswer') : t('testStatus', { status: String(status) });
    if (status !== null && status >= 200 && status < 300) toast.success(message);
    else toast.error(message);
    if (history?.webhook.id === webhook.id) void loadHistory(webhook);
  }

  async function remove({ id, label }: Webhook) {
    setError('');
    const result = await deleteWebhookAction(id);
    if (!result.success) {
      setError(errorText(result.error, t('actionError')));
      return;
    }
    setWebhooks((current) => current.filter((webhook) => webhook.id !== id));
    setHistory((current) => (current?.webhook.id === id ? null : current));
    toast.success(t('deletedToast', { label }));
  }

  const atCap = webhooks.length >= MAX_WEBHOOKS;
  return (
    <div className="flex flex-col gap-5">
      {!loadFailed && events.length > 0 && (
        <WebhookForm events={events} pending={pending} disabled={atCap} onSubmit={create} />
      )}
      {atCap && <p className="text-small text-muted">{t('capReached')}</p>}
      {secret && <SecretReveal secret={secret} onDismiss={() => setSecret(null)} />}
      {error && (
        <p role="alert" className="text-negative text-ui-strong">
          {error}
        </p>
      )}
      {loadFailed ? (
        <p role="alert" className="text-muted text-ui">
          {t('loadError')}
        </p>
      ) : webhooks.length === 0 ? (
        <p className="text-muted text-ui">{t('empty')}</p>
      ) : (
        <WebhookTable
          webhooks={webhooks}
          busy={testing !== null}
          testing={testing}
          historyOf={history?.webhook.id ?? null}
          onTest={(webhook) => void test(webhook)}
          onHistory={toggleHistory}
          onDelete={setDeleting}
        />
      )}
      {history && (
        <DeliveryHistory
          history={history}
          loading={loadingHistory}
          onRefresh={() => void loadHistory(history.webhook)}
        />
      )}
      <p className="text-muted text-small">
        {t('capUsed', { used: webhooks.length, max: MAX_WEBHOOKS })}
      </p>
      <p className="text-muted text-small">
        {t('signingHelp')}{' '}
        <a
          href="https://www.standardwebhooks.com"
          target="_blank"
          rel="noopener noreferrer"
          className="text-ui-strong text-accent-ink"
        >
          {t('signingDocs')}
        </a>
      </p>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t('deleteTitle', { label: deleting?.label ?? '' })}
        description={t('deleteBody')}
        confirmLabel={t('deleteConfirm')}
        consequences={[t('deleteBullet1'), t('deleteBullet2')]}
        onConfirm={() => deleting && remove(deleting)}
      />
    </div>
  );
}
