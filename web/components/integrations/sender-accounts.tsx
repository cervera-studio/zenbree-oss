'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { setSenderLinkAction } from '@/app/(app)/settings/senders/actions';
import { ProviderIcon } from '@/components/integrations/provider-icon';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';
import type { SenderAccount } from '@/lib/integrations/schemas';

type Client = { id: string; name: string };

/** The organization's accounts and the clients using each (#327): whoever connected one, or an admin, links it
 * to another client or unlinks it. Unlinking the last client leaves it connected, listed here. */
export function SenderAccounts({
  accounts,
  clients,
}: {
  accounts: SenderAccount[];
  clients: Client[];
}) {
  const t = useTranslations('senders.accounts');
  const [open, setOpen] = useState<string | null>(null);
  const names = new Map(clients.map((c) => [c.id, c.name]));
  const editing = accounts.find((a) => a.id === open);

  return (
    <>
      <ul className="flex flex-col">
        {accounts.map((a) => (
          <li
            key={a.id}
            className="border-hair flex flex-wrap items-center gap-3 border-b py-3 first:pt-0 last:border-0 last:pb-0"
          >
            {a.provider === 'unipile' ? (
              <span
                aria-hidden
                className="bg-accent text-on-accent flex size-5 shrink-0 items-center justify-center rounded-sm text-xs font-bold"
              >
                in
              </span>
            ) : (
              <ProviderIcon provider={a.provider} />
            )}
            <div className="flex min-w-0 grow basis-60 flex-col gap-0.5">
              <span className="text-ui-strong truncate">{a.accountLabel ?? a.provider}</span>
              <span className="text-small text-muted">
                {a.projectIds.length
                  ? t('usedBy', {
                      clients: a.projectIds.map((id) => names.get(id) ?? '…').join(', '),
                    })
                  : t('unused')}
                {a.connectedBy && ` · ${t('connectedBy', { name: a.connectedBy })}`}
              </span>
            </div>
            {a.canManage && (
              <Button type="button" variant="outline" size="xs" onClick={() => setOpen(a.id)}>
                {t('useElsewhere')}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <Dialog open={editing !== undefined} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent>
          {editing && <LinkClients account={editing} clients={clients} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function LinkClients({ account, clients }: { account: SenderAccount; clients: Client[] }) {
  const t = useTranslations('senders.accounts');
  const errorText = useErrorText();
  const { pending, run } = useAction();
  const linked = new Set(account.projectIds);
  const label = account.accountLabel ?? account.provider;

  const toggle = (client: Client, on: boolean) =>
    run(() => setSenderLinkAction(account.id, client.id, on), {
      successToast: t(on ? 'linked' : 'unlinked', { client: client.name }),
      errorToast: (error) =>
        error.code === 'INTEGRATIONS.REPLACE_FORBIDDEN'
          ? t('replaceForbidden', { client: client.name })
          : errorText(error, t('linkError')),
    });

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t('dialogTitle', { account: label })}</DialogTitle>
        <p className="text-ui text-muted">{t('dialogBody')}</p>
      </DialogHeader>
      <ul className="flex flex-col">
        {clients.map((c) => (
          <li
            key={c.id}
            className="border-hair flex items-center gap-3 border-b py-2.5 last:border-0"
          >
            <label htmlFor={`link-${c.id}`} className="text-ui min-w-0 grow truncate font-medium">
              {c.name}
            </label>
            <Switch
              id={`link-${c.id}`}
              checked={linked.has(c.id)}
              disabled={pending}
              onCheckedChange={(on) => toggle(c, on)}
            />
          </li>
        ))}
      </ul>
    </>
  );
}
