'use client';

import { Loader2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import {
  resolveHubSpotTimelineAction,
  retryHubSpotTimelineAction,
} from '@/app/(app)/settings/integrations/actions';
import { Badge, type Tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import type { components } from '@/lib/api/schema.gen';
import { useAction } from '@/lib/hooks/use-action';
import type { HubSpotTimeline } from '@/lib/integrations/schemas';

type TimelineCode = components['schemas']['TimelineIssue']['code'];
type Issue = HubSpotTimeline['issues'][number];

// One message per code of the API. A code the API adds fails the typecheck here once `pnpm api:types` has
// run, instead of reaching the user as a blank line.
const TEXT_KEY = {
  'CRM_TIMELINE.NO_OWNER': 'noOwner',
  'CRM_TIMELINE.OUTSIDE_WRITE_BACK': 'outsideWriteBack',
  'CRM_TIMELINE.DUPLICATE': 'duplicate',
  'CRM_TIMELINE.NATIVE_DUPLICATE': 'nativeDuplicate',
  'CRM_TIMELINE.REFUSED': 'refused',
  'CRM_TIMELINE.CONTACT_REFUSED': 'contactRefused',
  'CRM_TIMELINE.ANSWER_LOST': 'answerLost',
  'CRM_TIMELINE.OWNER_LEFT': 'ownerLeft',
  'CRM_TIMELINE.GRANT_REVOKED': 'grantRevoked',
  'CRM_TIMELINE.HELD': 'held',
  'CRM_TIMELINE.VENDOR_DISABLED': 'vendorDisabled',
} as const satisfies Record<TimelineCode, string>;

const TONE = {
  waiting: 'neutral',
  skipped: 'neutral',
  failed: 'negative',
  uncertain: 'warning',
} as const satisfies Record<Issue['state'], Tone>;

function textKey(code: string) {
  return Object.hasOwn(TEXT_KEY, code) ? TEXT_KEY[code as TimelineCode] : 'unknown';
}

/**
 * What reached the contacts' timelines from the tools Zenbree does not send with (#932), and what did not: a
 * line per reason, with the action when the user can take one. A failed write is retried; an unsure one is
 * the user's call, between a possible duplicate and a possible gap.
 */
export function HubSpotTimelineStatus({ timeline }: { timeline: HubSpotTimeline }) {
  const t = useTranslations('integrations.hubspot.timeline');
  const format = useFormatter();
  const errorText = useErrorText();
  const { pending, run } = useAction();

  function act(
    action: () => ReturnType<typeof retryHubSpotTimelineAction>,
    toastKey: 'retriedToast' | 'markedToast',
  ) {
    run(action, {
      onSuccess: (n) => toast.success(n > 0 ? t(toastKey, { n }) : t('noneToast')),
      errorToast: (error) => errorText(error, t('failed')),
    });
  }

  const empty = timeline.logged === 0 && timeline.pending === 0 && timeline.issues.length === 0;
  return (
    <div className="border-hair flex flex-col gap-2 border-t pt-3">
      <span className="text-ui-strong">{t('title')}</span>
      <p className="text-small text-muted">{t('help')}</p>
      {empty ? (
        <p className="text-small text-muted">{t('empty')}</p>
      ) : (
        <p className="text-small text-muted flex flex-wrap gap-x-3">
          <span>{t('logged', { n: timeline.logged })}</span>
          <span>{t('pending', { n: timeline.pending })}</span>
        </p>
      )}
      {timeline.issues.length > 0 && (
        <ul className="flex flex-col gap-2">
          {timeline.issues.map((issue) => (
            <li
              key={`${issue.state}:${issue.code}`}
              className="border-line flex flex-col gap-2 rounded-md border p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[issue.state]} dot>
                  {t(`states.${issue.state}`)}
                </Badge>
                <span className="text-small text-muted">
                  {t('latest', {
                    date: format.dateTime(new Date(issue.latestAt), {
                      day: 'numeric',
                      month: 'short',
                    }),
                  })}
                </span>
              </div>
              <p className="text-small">
                {t(`codes.${textKey(issue.code)}`, { count: issue.count })}
              </p>
              {issue.action === 'retry' && (
                <div>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={pending}
                    onClick={() =>
                      act(() => retryHubSpotTimelineAction(issue.code), 'retriedToast')
                    }
                  >
                    {pending && <Loader2 className="animate-spin" />}
                    {t('retry')}
                  </Button>
                </div>
              )}
              {issue.action === 'resolve' && (
                <div className="flex flex-col gap-2">
                  <p className="text-small text-muted">{t('uncertainHint')}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={pending}
                      onClick={() =>
                        act(
                          () => resolveHubSpotTimelineAction(issue.code, 'log_again'),
                          'retriedToast',
                        )
                      }
                    >
                      {t('logAgain')}
                    </Button>
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={pending}
                      onClick={() =>
                        act(() => resolveHubSpotTimelineAction(issue.code, 'logged'), 'markedToast')
                      }
                    >
                      {t('markLogged')}
                    </Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
