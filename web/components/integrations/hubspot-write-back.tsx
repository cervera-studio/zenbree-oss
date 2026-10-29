'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import {
  connectHubSpotAction,
  setHubSpotWriteBackAction,
} from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { Switch } from '@/components/ui/switch';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';

/**
 * The user's switch for letting Zenbree write in their HubSpot (#526). HubSpot's consent screen lists the
 * write permissions as optional checkboxes: a grant without the contacts one offers a reconnection instead of
 * the switch, and one without the companies one says what it leaves out.
 */
export function HubSpotWriteBack({
  canWrite,
  canWriteCompanies,
  enabled,
}: {
  canWrite: boolean;
  canWriteCompanies: boolean;
  enabled: boolean;
}) {
  const t = useTranslations('integrations.hubspot.writeBack');
  const id = useId();
  const [on, setOn] = useState(enabled);
  const errorText = useErrorText();
  const { pending, run } = useAction();

  function toggle(next: boolean) {
    setOn(next);
    run(() => setHubSpotWriteBackAction(next), {
      onSuccess: (enabled) => {
        setOn(enabled);
        toast.success(t(enabled ? 'onToast' : 'offToast'));
      },
      onError: () => setOn(!next),
      errorToast: (error) => errorText(error, t('failed')),
    });
  }

  function reconnect() {
    run(connectHubSpotAction, {
      onSuccess: (url) => window.location.assign(url),
      errorToast: t('reconnectFailed'),
    });
  }

  return (
    <div className="border-hair flex flex-col gap-2 border-t pt-3">
      <div className="flex items-center gap-3">
        {canWrite ? (
          <>
            <label htmlFor={id} className="text-ui-strong grow">
              {t('label')}
            </label>
            <Switch
              id={id}
              checked={on}
              disabled={pending}
              onCheckedChange={toggle}
              aria-describedby={`${id}-help`}
            />
          </>
        ) : (
          <>
            {/* Not a label: the button would be announced as «Scrivi su HubSpot» while it reads «Ricollega». */}
            <span className="text-ui-strong grow">{t('label')}</span>
            <Button
              variant="outline"
              size="xs"
              onClick={reconnect}
              disabled={pending}
              aria-describedby={`${id}-help`}
            >
              {pending && <Loader2 className="animate-spin" />}
              {t('reconnect')}
            </Button>
          </>
        )}
      </div>
      <p id={`${id}-help`} className="text-small text-muted">
        {t(canWrite ? 'help' : 'needsPermission')}
      </p>
      {canWrite && !canWriteCompanies && (
        <p className="text-small text-muted flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="min-w-0 grow basis-60">{t('noCompanies')}</span>
          <Button variant="outline" size="xs" onClick={reconnect} disabled={pending}>
            {t('reconnect')}
          </Button>
        </p>
      )}
    </div>
  );
}
