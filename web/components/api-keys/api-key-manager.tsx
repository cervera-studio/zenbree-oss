'use client';

import { Check, Copy, Loader2, Plus } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { createApiKeyAction, revokeApiKeyAction } from '@/app/(app)/settings/api-keys/actions';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useErrorText } from '@/i18n/error-text';
import type { ApiKey, IssuedApiKey } from '@/lib/api-keys';
import { getPublicApiBaseUrl } from '@/lib/config/env';
import { useCopy } from '@/lib/hooks/use-copy';

export function ApiKeyManager({
  initialKeys,
  loadFailed,
}: {
  initialKeys: ApiKey[];
  loadFailed: boolean;
}) {
  const t = useTranslations('apiKeys');
  const errorText = useErrorText();
  const format = useFormatter();
  const [keys, setKeys] = useState(initialKeys);
  const [failedToLoad, setFailedToLoad] = useState(loadFailed);
  const [label, setLabel] = useState('');
  const [newKey, setNewKey] = useState<IssuedApiKey | null>(null);
  const { copy, copied, resetCopied } = useCopy();
  const [error, setError] = useState('');
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [pending, startTransition] = useTransition();

  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    startTransition(async () => {
      const result = await createApiKeyAction(label);
      if (!result.success) {
        setError(errorText(result.error, t('actionError')));
        return;
      }
      const { key, ...metadata } = result.data;
      setNewKey({ ...metadata, key });
      resetCopied();
      setKeys((current) => [metadata, ...current]);
      setFailedToLoad(false);
      setLabel('');
    });
  }

  async function copyKey() {
    if (!newKey) return;
    if (!(await copy(newKey.key))) setError(t('copyFailed'));
  }

  async function revoke({ id, label }: ApiKey) {
    setError('');
    const result = await revokeApiKeyAction(id);
    if (!result.success) {
      setError(errorText(result.error, t('actionError')));
      return;
    }
    setKeys((current) =>
      current.map((key) => (key.id === id ? { ...key, revokedAt: result.data.revokedAt } : key)),
    );
    setNewKey((current) => (current?.id === id ? null : current));
    toast.success(t('revokedToast', { label }));
  }

  return (
    <div className="flex flex-col gap-5">
      <form
        className="border-line bg-raised flex flex-col gap-3 rounded-lg border p-4 desktop:flex-row desktop:items-end"
        onSubmit={create}
      >
        <div className="flex min-w-0 grow flex-col gap-1.5">
          <label className="text-small" htmlFor="api-key-label">
            {t('label')}
          </label>
          <Input
            id="api-key-label"
            className="phone:h-11"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={t('labelPlaceholder')}
            maxLength={120}
            required
          />
        </div>
        <Button type="submit" className="phone:h-11" disabled={pending || !label.trim()}>
          {pending ? <Loader2 className="animate-spin" /> : <Plus aria-hidden />}
          {t('create')}
        </Button>
      </form>

      {newKey && (
        <div role="status" className="bg-positive-tint flex flex-col gap-2.5 rounded-lg p-4">
          <p className="text-ui-strong text-positive">{t('createdOnce')}</p>
          <p className="text-small text-soft">{t('createdOnceHelp')}</p>
          <div className="flex gap-2">
            <Input
              aria-label={t('newKey')}
              className="font-mono text-data phone:h-11"
              readOnly
              value={newKey.key}
              onFocus={(event) => event.target.select()}
            />
            <Button
              type="button"
              variant="outline"
              className="phone:h-11"
              aria-label={copied ? t('copied') : t('copyKey')}
              onClick={() => void copyKey()}
            >
              {copied ? (
                <Check aria-hidden="true" className="size-4" />
              ) : (
                <Copy aria-hidden="true" className="size-4" />
              )}
              {copied ? t('copied') : t('copyKey')}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="text-negative text-ui-strong">
          {error}
        </p>
      )}
      {failedToLoad ? (
        <p role="alert" className="text-muted text-ui">
          {t('loadError')}
        </p>
      ) : keys.length === 0 ? (
        <p className="text-muted text-ui">{t('empty')}</p>
      ) : (
        <StackedTable frame>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('name')}</TableHead>
              <TableHead scope="col">{t('keyColumn')}</TableHead>
              <TableHead scope="col">{t('created')}</TableHead>
              <TableHead scope="col">{t('status')}</TableHead>
              <TableHead scope="col">
                <span className="sr-only">{t('actions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((key) => (
              <TableRow key={key.id}>
                <TableCell className="text-ui-strong text-ink">
                  <CellLabel>{t('name')}</CellLabel>
                  {key.label}
                </TableCell>
                <TableCell className="text-muted phone:order-2">
                  <CellLabel>{t('keyColumn')}</CellLabel>
                  {key.last4 ? (
                    <span className="text-data font-mono">sbk_…{key.last4}</span>
                  ) : (
                    t('keyUnknown')
                  )}
                </TableCell>
                <TableCell className="text-data font-mono text-soft phone:order-2">
                  <CellLabel>{t('created')}</CellLabel>
                  <time dateTime={key.createdAt}>
                    {format.dateTime(new Date(key.createdAt), DAY)}
                  </time>
                </TableCell>
                <TableCell className="phone:order-1">
                  <CellLabel>{t('status')}</CellLabel>
                  <Badge dot tone={key.revokedAt ? 'neutral' : 'positive'}>
                    {t(key.revokedAt ? 'revoked' : 'active')}
                  </Badge>
                </TableCell>
                <TableCell className="text-right phone:order-3">
                  {!key.revokedAt && (
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      className="text-negative border-negative phone:h-control-md"
                      disabled={pending}
                      aria-label={t('revokeLabel', { label: key.label })}
                      onClick={() => setRevoking(key)}
                    >
                      {t('revoke')}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </StackedTable>
      )}
      <p className="text-muted text-small">
        {t('creditsNote')}{' '}
        <a
          href={`${getPublicApiBaseUrl()}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-ui-strong text-accent-ink"
        >
          {t('docs')}
        </a>
      </p>
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={t('revokeTitle', { label: revoking?.label ?? '' })}
        description={t('revokeBody')}
        confirmLabel={t('revokeConfirm')}
        consequences={[t('revokeBullet1'), t('revokeBullet2')]}
        onConfirm={() => revoking && revoke(revoking)}
      />
    </div>
  );
}

const DAY = { day: 'numeric', month: 'short', year: 'numeric' } as const;
