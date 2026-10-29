import { LayoutDashboard } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import { HubSpotButton } from '@/components/integrations/hubspot-button';
import { HubSpotTimelineStatus } from '@/components/integrations/hubspot-timeline';
import { showsTimeline } from '@/components/integrations/hubspot-timeline-shows';
import { HubSpotWriteBack } from '@/components/integrations/hubspot-write-back';
import { IconTile, SettingsCard } from '@/components/settings/settings-card';
import { StatusNotice } from '@/components/settings/status-notice';
import { Badge, type Tone } from '@/components/ui/badge';
import type { HubSpotStatus } from '@/lib/integrations/schemas';

function pill(status: HubSpotStatus): {
  tone: Tone;
  key: 'connected' | 'needsReconnect' | 'disconnected';
} {
  if (status.connected) return { tone: 'positive', key: 'connected' };
  return status.status === 'error'
    ? { tone: 'warning', key: 'needsReconnect' }
    : { tone: 'neutral', key: 'disconnected' };
}

// `status` is null when the API could not be read; `outcome` is ?hubspot= from the OAuth callback.
export async function HubSpotCard({
  status,
  outcome,
  declined,
}: {
  status: HubSpotStatus | null;
  outcome?: string;
  declined: boolean;
}) {
  const t = await getTranslations('integrations.hubspot');
  const state = status ? pill(status) : null;
  return (
    <>
      {outcome === 'connected' && (
        <StatusNotice tone="positive">{t('connectedNotice')}</StatusNotice>
      )}
      {outcome === 'failed' && (
        <StatusNotice tone="negative">
          {t(declined ? 'declinedNotice' : 'failedNotice')}
        </StatusNotice>
      )}
      <section aria-labelledby="hubspot-name">
        <SettingsCard tone={status?.connected ? 'plain' : 'dashed'}>
          <div className="flex flex-wrap items-start gap-3">
            <IconTile>
              <LayoutDashboard className="size-4" />
            </IconTile>
            <div className="flex min-w-0 flex-1 basis-48 flex-col">
              <h3 id="hubspot-name" className="text-ui-strong">
                {t('name')}
              </h3>
              <p className="text-small text-muted">{t('description')}</p>
            </div>
            {state && (
              <Badge tone={state.tone} dot>
                {t(state.key)}
              </Badge>
            )}
            {status && <HubSpotButton connected={status.connected} disabled={!status.configured} />}
          </div>
          {status ? (
            <>
              {(status.accountLabel || !status.configured) && (
                <span className="text-small text-muted min-w-0 truncate">
                  {status.accountLabel ?? t('notConfigured')}
                </span>
              )}
              {status.connected && (
                <HubSpotWriteBack
                  canWrite={status.canWrite}
                  canWriteCompanies={status.canWriteCompanies}
                  enabled={status.writeBack}
                />
              )}
              {showsTimeline(status) && <HubSpotTimelineStatus timeline={status.timeline} />}
            </>
          ) : (
            <p className="text-small text-muted">{t('statusError')}</p>
          )}
        </SettingsCard>
      </section>
    </>
  );
}
