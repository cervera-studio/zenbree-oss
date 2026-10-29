'use client';

import { KeyRound, Loader2, RotateCw, TriangleAlert } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRef, useState } from 'react';
import { makeWebhookSecretAction } from '@/app/(app)/automations/actions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Panel } from '@/components/ui/panel';
import { useErrorText } from '@/i18n/error-text';
import type { Automation, WebhookSecret } from '@/lib/automations/api';
import { useAction } from '@/lib/hooks/use-action';
import { CopyField } from './copy-field';
import { useWhen } from './labels';

// The secret lives only in `shown` until the dialog's exit ends: then it is dropped, and nothing fetches it again.
type Secret = { phase: 'confirmRotate' } | { phase: 'shown'; made: WebhookSecret };

const EXAMPLE_BODY =
  '{"email": "ada@example.com", "linkedin_url": "https://www.linkedin.com/in/ada"}';

export function WebhookSetup({
  automation,
  manage,
}: {
  automation: Pick<Automation, 'id' | 'name' | 'enabled' | 'webhook'>;
  manage: boolean;
}) {
  const t = useTranslations('automations.webhook');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const when = useWhen();
  const { pending, error, run, reset } = useAction();
  const [open, setOpen] = useState(false);
  // Kept through the exit animation, so the closing dialog never falls back to the destructive rotate prompt.
  const [secret, setSecret] = useState<Secret | null>(null);
  const show = (next: Secret) => {
    setSecret(next);
    setOpen(true);
  };
  // The first secret replaces the button that made it, so focus goes back to Rotate.
  const rotateButton = useRef<HTMLButtonElement>(null);
  const hook = automation.webhook;

  const make = () =>
    run(() => makeWebhookSecretAction(automation.id), {
      onSuccess: (made) => show({ phase: 'shown', made }),
    });

  return (
    <Panel
      title={t('title')}
      action={
        manage &&
        hook && (
          <Button
            ref={rotateButton}
            variant="outline"
            size="sm"
            className="phone:h-11"
            onClick={() => {
              reset();
              show({ phase: 'confirmRotate' });
            }}
          >
            <RotateCw aria-hidden />
            {t('rotate')}
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-5">
        {hook ? (
          <div className="flex flex-col gap-3">
            {hook.url ? (
              <CopyField label={t('url')} value={hook.url} />
            ) : (
              <p className="text-ui">{t('noUrl')}</p>
            )}
            <p className="text-small text-muted">
              {t('secretMade', { when: when(hook.rotatedAt) })}
            </p>
            <Queue pending={hook.pending} waiting={hook.waitingForApproval} />
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3">
            <p className="text-ui text-ink">{manage ? t('noSecret') : t('noSecretReadOnly')}</p>
            {manage && (
              <Button className="phone:h-11" disabled={pending} onClick={make}>
                {pending ? (
                  <Loader2 aria-hidden className="animate-spin" />
                ) : (
                  <KeyRound aria-hidden />
                )}
                {t('make')}
              </Button>
            )}
            {error && !open && (
              <p role="alert" className="text-negative text-small">
                {errorText(error)}
              </p>
            )}
          </div>
        )}
        {!automation.enabled && <p className="text-small text-warning">{t('paused')}</p>}
        <HowToCall />
      </div>
      <Dialog open={open} onOpenChange={(next) => !next && !pending && setOpen(false)}>
        <DialogContent
          className="sm:max-w-xl"
          // A stray click outside must not throw away a secret that is never shown again.
          onInteractOutside={(event) => secret?.phase === 'shown' && event.preventDefault()}
          onCloseAutoFocus={(event) => {
            setSecret(null);
            if (!rotateButton.current) return;
            event.preventDefault();
            rotateButton.current.focus();
          }}
        >
          {secret?.phase === 'shown' ? (
            <Shown made={secret.made} onDone={() => setOpen(false)} />
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{t('rotateTitle', { name: automation.name })}</DialogTitle>
                <DialogDescription>{t('rotateText')}</DialogDescription>
              </DialogHeader>
              <ul className="text-muted flex list-disc flex-col gap-1 pl-4.5 text-xs-plus leading-normal">
                <li>{t('rotateUrlStays')}</li>
                <li>{t('rotateUpdateSender')}</li>
              </ul>
              {error && (
                <p role="alert" className="text-negative text-small">
                  {errorText(error)}
                </p>
              )}
              <DialogFooter>
                <Button variant="outline" disabled={pending} onClick={() => setOpen(false)}>
                  {tc('cancel')}
                </Button>
                <Button variant="negative" disabled={pending} onClick={make}>
                  {pending && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
                  {t('rotateConfirm')}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

function Shown({ made, onDone }: { made: WebhookSecret; onDone: () => void }) {
  const t = useTranslations('automations.webhook');
  return (
    <>
      <DialogHeader>
        <DialogTitle>{t('shownTitle')}</DialogTitle>
        <DialogDescription>{t('shownText')}</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        {made.url && <CopyField label={t('url')} value={made.url} />}
        <CopyField label={t('secret')} value={made.secret} autoFocus />
      </div>
      <DialogFooter>
        <Button onClick={onDone}>{t('done')}</Button>
      </DialogFooter>
    </>
  );
}

function Queue({ pending, waiting }: { pending: number; waiting: boolean }) {
  const t = useTranslations('automations.webhook');
  if (!waiting && pending === 0) return <p className="text-small text-muted">{t('noneWaiting')}</p>;
  return (
    <div className="flex flex-col gap-2">
      {pending > 0 && <p className="text-ui text-ink">{t('pending', { n: pending })}</p>}
      {waiting && (
        <p className="bg-warning-tint text-warning text-ui flex gap-2 rounded-md p-3">
          <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          {t('waitingForApproval')}
        </p>
      )}
    </div>
  );
}

function HowToCall() {
  const t = useTranslations('automations.webhook.howTo');
  return (
    <section aria-labelledby="webhook-how-to" className="flex flex-col gap-2">
      <h3 id="webhook-how-to" className="text-ui-strong text-ink">
        {t('title')}
      </h3>
      <ol className="text-ui flex list-decimal flex-col gap-2 pl-4.5">
        <li>{t('body')}</li>
        <li>
          {t('sign')}{' '}
          <a
            href="https://www.standardwebhooks.com"
            target="_blank"
            rel="noopener noreferrer"
            className="text-ui-strong text-accent-ink hover:underline"
          >
            {t('signDocs')}
          </a>
        </li>
        <li>{t('answer')}</li>
      </ol>
      <pre className="bg-side text-data-sm text-ink overflow-x-auto rounded-md px-3 py-2 font-mono whitespace-pre-wrap break-all">
        {EXAMPLE_BODY}
      </pre>
    </section>
  );
}
