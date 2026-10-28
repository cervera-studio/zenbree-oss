'use client';

import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { moveInstantlyStopAction } from '@/app/(app)/settings/integrations/actions';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';
import type { InstantlyStop, InstantlyStops as Stops } from '@/lib/integrations/instantly-stops';

/** `now` comes from the server render, so the server and the browser print the same next attempt. */
export function InstantlyStops({
  stops,
  connected,
  now,
}: {
  stops: Stops;
  connected: boolean;
  now: string;
}) {
  const t = useTranslations('integrations.instantly.stops');
  const errorText = useErrorText();
  const router = useRouter();
  const { pending, error, run } = useAction();
  if (stops.items.length === 0) return null;

  const move = (id: string, verb: 'retry' | 'confirm') =>
    run(() => moveInstantlyStopAction(id, verb), { onSuccess: () => router.refresh() });

  return (
    <div className="flex flex-col gap-3 border-t pt-3">
      <div className="flex flex-col gap-0.5">
        <h4 className="text-ui-strong">{t('title')}</h4>
        <p className="text-small text-muted">
          {t('counts', { pending: stops.pending, failed: stops.failed })}
        </p>
      </div>
      {stops.paused && <p className="bg-warning-tint text-ui rounded-lg p-3.5">{t('paused')}</p>}
      {error && (
        <p role="alert" className="text-small text-negative">
          {errorText(error)}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {stops.items.map((stop) => (
          <StopRow
            key={stop.id}
            stop={stop}
            now={new Date(now)}
            canRetry={connected}
            canConfirm={stops.mayConfirm}
            busy={pending}
            onMove={(verb) => move(stop.id, verb)}
          />
        ))}
      </ul>
    </div>
  );
}

const STOP_TEXT = /^INSTANTLY\.(UNREACHABLE|UNKNOWN_OUTCOME|RATE_LIMITED|REJECTED|NOT_FOUND)$/;
const LATER = /^INSTANTLY\.(UNREACHABLE|UNKNOWN_OUTCOME|RATE_LIMITED|LEAD_NOT_SETTLED)$/;

function StopRow({
  stop,
  now,
  canRetry,
  canConfirm,
  busy,
  onMove,
}: {
  stop: InstantlyStop;
  now: Date;
  canRetry: boolean;
  canConfirm: boolean;
  busy: boolean;
  onMove: (verb: 'retry' | 'confirm') => void;
}) {
  const t = useTranslations('integrations.instantly.stops');
  const errorText = useErrorText();
  const format = useFormatter();
  const failed = stop.status === 'failed';
  const code = stop.errorCode ?? '';
  const failedText = STOP_TEXT.test(code)
    ? t(`failed.${code.slice('INSTANTLY.'.length)}` as 'failed.UNREACHABLE')
    : errorText({ code });
  const next = stop.nextAttemptAt ? new Date(stop.nextAttemptAt) : null;
  return (
    <li className="border-line flex flex-col gap-1.5 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-ui-strong text-ink min-w-0 truncate">{stop.email}</span>
        <Badge tone={failed ? 'negative' : 'warning'}>{t(`status.${stop.status}`)}</Badge>
      </div>
      <p className="text-small text-muted">{t(`reason.${stop.reason}`)}</p>
      {failed && code && <p className="text-small text-ink">{failedText}</p>}
      {!failed && stop.attempts > 0 && next && (
        <p className="text-small text-muted">
          {LATER.test(code)
            ? t(`later.${code.slice('INSTANTLY.'.length)}` as 'later.UNREACHABLE')
            : t('later.other')}{' '}
          {next > now ? t('nextAttempt', { when: format.relativeTime(next, now) }) : t('dueNow')}
        </p>
      )}
      {failed && (canRetry || canConfirm) && (
        <div className="flex flex-wrap gap-2">
          {canRetry && (
            <Button variant="outline" size="xs" disabled={busy} onClick={() => onMove('retry')}>
              {t('retry')}
            </Button>
          )}
          {canConfirm && (
            <Button variant="outline" size="xs" disabled={busy} onClick={() => onMove('confirm')}>
              {t('confirm')}
            </Button>
          )}
        </div>
      )}
    </li>
  );
}
