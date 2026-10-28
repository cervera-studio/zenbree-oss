import { getFormatter, getTranslations } from 'next-intl/server';
import { errorText } from '@/i18n/error-text';
import type { InstantlySync as Sync } from '@/lib/integrations/schemas';

// These codes say what to fix in Instantly; the others name a batch step that a sync pass does not take.
const ACTIONABLE = new Set([
  'INSTANTLY.INVALID_KEY',
  'INSTANTLY.PLAN_REQUIRED',
  'INSTANTLY.MISSING_SCOPE',
]);

// #930: how the campaign events reach Zenbree, and how fresh they are. It never says "live" before a
// webhook delivered: a polled workspace reads "within minutes".
export async function InstantlySync({ sync }: { sync: Sync }) {
  const [t, errors, format] = await Promise.all([
    getTranslations('integrations.instantly.sync'),
    getTranslations('errors'),
    getFormatter(),
  ]);
  if (sync.mode === 'off') return null;
  const now = new Date();
  const ago = (at: string) => format.relativeTime(new Date(at), now);
  const code = sync.lastErrorCode;
  return (
    <div className="text-small text-muted flex flex-col gap-1">
      <p className="text-ui">{t(`mode.${sync.mode}`)}</p>
      {sync.mode === 'live' && !sync.emailsRefused && <p>{t('replySorting')}</p>}
      {sync.mode === 'polled' && sync.webhookRefusal && (
        <p>{t(`refusal.${sync.webhookRefusal}`)}</p>
      )}
      {sync.emailsRefused ? (
        <p>{t('emailsRefused')}</p>
      ) : (
        <p>
          {sync.completeUntil
            ? t('completeUntil', { ago: ago(sync.completeUntil) })
            : t('firstPass')}
        </p>
      )}
      {sync.lastAttemptAt && <p>{t('lastCheck', { ago: ago(sync.lastAttemptAt) })}</p>}
      {code && (
        <p role="status">
          {t('failing', {
            since: format.dateTime(new Date(sync.failingSince ?? sync.lastAttemptAt ?? now), {
              dateStyle: 'medium',
              timeStyle: 'short',
            }),
            reason: ACTIONABLE.has(code) ? errorText(errors, { code }) : t('failingReason'),
          })}
        </p>
      )}
    </div>
  );
}
