import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { IconTile, SettingsItem } from '@/components/settings/settings-card';
import { Badge } from '@/components/ui/badge';
import type { MailboxProvider } from '@/lib/integrations/schemas';
import { MailboxButton } from './mailbox-button';
import { ProviderIcon } from './provider-icon';

export function MailboxRow({
  id,
  provider,
  accountLabel,
  connected,
  disabled,
  canDisconnect,
  canReadBounces,
}: {
  id: string;
  provider: MailboxProvider;
  accountLabel?: string | null;
  connected: boolean;
  disabled: boolean;
  /** #307: the mailbox is the project's; another member uses it but cannot disconnect it. */
  canDisconnect: boolean;
  /** #747: connected before the bounce read permission, or with it unticked: it sends, but a reconnect reads its bounces. */
  canReadBounces: boolean;
}) {
  const t = useTranslations('integrations.mailbox');
  return (
    <SettingsItem>
      <IconTile>
        <ProviderIcon provider={provider} />
      </IconTile>
      <div className="flex min-w-0 grow flex-col">
        <span className="text-ui-strong">{t(`providers.${provider}`)}</span>
        {accountLabel && <span className="text-small text-muted truncate">{accountLabel}</span>}
        {connected && !canReadBounces && (
          <span className="text-small text-muted">{t('readBouncesNeeded')}</span>
        )}
      </div>
      <Badge tone={connected ? 'positive' : 'warning'} dot>
        {t(connected ? 'connected' : 'needsReconnect')}
      </Badge>
      {connected && (
        <Link
          href="/settings/senders"
          className="text-ui-strong text-accent-ink rounded-sm hover:underline focus-visible:ring-3 focus-visible:ring-accent/50 focus-visible:outline-none"
        >
          {t('senders')}
        </Link>
      )}
      {connected && !canReadBounces && (
        <MailboxButton provider={provider} mode="reconnect" disabled={disabled} />
      )}
      {(canDisconnect || !connected) && (
        <MailboxButton
          provider={provider}
          {...(connected ? { mode: 'disconnect', senderId: id } : { mode: 'reconnect' })}
          disabled={disabled}
        />
      )}
    </SettingsItem>
  );
}
