import { createLucideIcon } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import { HubSpotCard } from '@/components/integrations/hubspot-card';
import { InstantlyCard } from '@/components/integrations/instantly-card';
import { LinkedInAccount } from '@/components/integrations/linkedin-account';
import { LinkedInConnect } from '@/components/integrations/linkedin-connect';
import { MailboxButton } from '@/components/integrations/mailbox-button';
import { MailboxRow } from '@/components/integrations/mailbox-row';
import { IconTile } from '@/components/settings/settings-card';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { SettingsSection } from '@/components/settings/settings-section';
import { StatusNotice } from '@/components/settings/status-notice';
import { Badge } from '@/components/ui/badge';
import { getHubSpot } from '@/lib/integrations/hubspot';
import { getInstantly } from '@/lib/integrations/instantly';
import { getInstantlyStops } from '@/lib/integrations/instantly-stops';
import { getLinkedIn } from '@/lib/integrations/linkedin';
import { getMailboxes } from '@/lib/integrations/mailboxes';

// lucide-react 1.0 dropped brand icons; this is its former LinkedIn glyph.
const Linkedin = createLucideIcon('linkedin', [
  [
    'path',
    {
      d: 'M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z',
      key: 'c2jq9f',
    },
  ],
  ['rect', { width: '4', height: '12', x: '2', y: '9', key: 'mk3on5' }],
  ['circle', { cx: '4', cy: '4', r: '2', key: 'bt5ra8' }],
]);

export default async function IntegrationsPage({
  searchParams,
}: {
  searchParams: Promise<{ mailbox?: string; hubspot?: string; reason?: string }>;
}) {
  const [t, linkedIn, mailboxes, hubspot, instantly, stops, sp] = await Promise.all([
    getTranslations('integrations'),
    getLinkedIn(),
    getMailboxes(),
    getHubSpot(),
    getInstantly(),
    getInstantlyStops(),
    searchParams,
  ]);
  const connected = linkedIn.success && linkedIn.data.connected;
  // ?mailbox= / ?hubspot= are set by the API's OAuth callback redirect.
  const declined = sp.reason === 'DECLINED' || sp.reason === 'INTEGRATIONS.SCOPE_DECLINED';
  const boxes = mailboxes.success ? mailboxes.data.mailboxes : [];
  const providers = mailboxes.success ? mailboxes.data.providers : [];

  return (
    <div className="@container flex flex-col gap-4">
      <SettingsPageHeader title={t('title')} description={t('description')} />
      {sp.mailbox === 'connected' && (
        <StatusNotice tone="positive">{t('mailbox.connectedNotice')}</StatusNotice>
      )}
      {sp.mailbox === 'failed' && (
        <StatusNotice tone="negative">
          {t(declined ? 'mailbox.declinedNotice' : 'mailbox.failedNotice')}
        </StatusNotice>
      )}
      <div className="flex flex-col gap-4 @5xl:flex-row @5xl:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <SettingsSection title={t('mailbox.name')} description={t('mailbox.description')}>
            {!mailboxes.success && (
              <p className="text-small text-muted">{t('mailbox.statusError')}</p>
            )}
            <div className="flex flex-col gap-2.5">
              {boxes.map((m) => (
                <MailboxRow
                  key={m.id}
                  id={m.id}
                  provider={m.provider}
                  accountLabel={m.accountLabel}
                  connected={m.connected}
                  disabled={!m.configured}
                  canDisconnect={m.canDisconnect}
                  canReadBounces={m.canReadBounces}
                />
              ))}
              {providers.map((m) => (
                <div
                  key={m.provider}
                  className="flex flex-wrap items-center justify-center gap-x-3"
                >
                  <MailboxButton
                    provider={m.provider}
                    mode="add"
                    disabled={!m.configured}
                    another={boxes.length > 0}
                  />
                  {!m.configured && (
                    <span className="text-small text-muted">{t('mailbox.notConfigured')}</span>
                  )}
                </div>
              ))}
            </div>
          </SettingsSection>
          <HubSpotCard
            status={hubspot.success ? hubspot.data : null}
            outcome={sp.hubspot}
            declined={declined}
          />
          <InstantlyCard
            status={instantly.success ? instantly.data : null}
            stops={stops.success ? stops.data : null}
          />
        </div>
        <div className="@5xl:w-135 @5xl:shrink-0">
          <SettingsSection title={t('linkedin.name')}>
            <div className="flex items-center gap-3">
              <IconTile>
                <Linkedin className="size-4" />
              </IconTile>
              <span className="text-small text-soft grow">{t('linkedin.description')}</span>
              <Badge tone={connected ? 'positive' : 'neutral'} dot>
                {!linkedIn.success
                  ? t('linkedin.statusUnknown')
                  : t(connected ? 'linkedin.connected' : 'linkedin.disconnected')}
              </Badge>
            </div>
            {!linkedIn.success && (
              <p className="text-small text-muted">{t('linkedin.statusError')}</p>
            )}
            {linkedIn.success && linkedIn.data.connected ? (
              <LinkedInAccount
                label={linkedIn.data.accountLabel}
                premium={linkedIn.data.premium === true}
                canDisconnect={linkedIn.data.canDisconnect}
              />
            ) : (
              // ponytail: an unreadable status still offers Connect — "unknown" is not "connected".
              // The API's `risk_notice` is Italian only: the page shows its own, in the user's language (#699).
              <LinkedInConnect riskNotice={t('linkedin.riskDefault')} />
            )}
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}
