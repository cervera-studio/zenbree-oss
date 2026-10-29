import { Send } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import { InstantlyBatch } from '@/components/integrations/instantly-batch';
import { InstantlyForm } from '@/components/integrations/instantly-form';
import { InstantlyStops } from '@/components/integrations/instantly-stops';
import { InstantlySync } from '@/components/integrations/instantly-sync';
import { IconTile, SettingsCard } from '@/components/settings/settings-card';
import { Badge, type Tone } from '@/components/ui/badge';
import type { InstantlyStops as Stops } from '@/lib/integrations/instantly-stops';
import type { InstantlyStatus } from '@/lib/integrations/schemas';

function pill(status: InstantlyStatus): {
  tone: Tone;
  key: 'connected' | 'needsReconnect' | 'disconnected';
} {
  if (status.connected) return { tone: 'positive', key: 'connected' };
  return status.status === 'error'
    ? { tone: 'warning', key: 'needsReconnect' }
    : { tone: 'neutral', key: 'disconnected' };
}

export async function InstantlyCard({
  status,
  stops,
}: {
  status: InstantlyStatus | null;
  stops: Stops | null;
}) {
  const t = await getTranslations('integrations.instantly');
  const state = status ? pill(status) : null;
  return (
    <section aria-labelledby="instantly-name">
      <SettingsCard tone={status?.connected ? 'plain' : 'dashed'}>
        <div className="flex flex-wrap items-start gap-3">
          <IconTile>
            <Send className="size-4" />
          </IconTile>
          <div className="flex min-w-0 flex-1 basis-48 flex-col">
            <h3 id="instantly-name" className="text-ui-strong">
              {t('name')}
            </h3>
            <p className="text-small text-muted">{t('description')}</p>
          </div>
          {state && (
            <Badge tone={state.tone} dot>
              {t(state.key)}
            </Badge>
          )}
        </div>
        {status ? (
          <>
            {status.connected && (
              <span className="text-small text-muted min-w-0 truncate">
                {status.workspaceName ?? status.name}
              </span>
            )}
            {status.connected && status.sync && <InstantlySync sync={status.sync} />}
            {status.status === 'error' && <p className="text-small text-muted">{t('errorHint')}</p>}
            {!status.configured && <p className="text-small text-muted">{t('notConfigured')}</p>}
            {stops && (
              <InstantlyStops
                stops={stops}
                connected={status.connected}
                now={new Date().toISOString()}
              />
            )}
            <InstantlyBatch connected={status.connected} />
            <InstantlyForm
              connected={status.connected}
              disabled={!status.configured || !status.canManage}
            />
          </>
        ) : (
          <p className="text-small text-muted">{t('statusError')}</p>
        )}
      </SettingsCard>
    </section>
  );
}
