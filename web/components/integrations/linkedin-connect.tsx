'use client';

import { Loader2, TriangleAlert } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type FormEvent, useId, useReducer } from 'react';
import {
  connectLinkedInAction,
  linkedInCheckpointAction,
} from '@/app/(app)/settings/integrations/actions';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAction } from '@/lib/hooks/use-action';
import type { ConnectOutcome } from '@/lib/integrations/schemas';
import type { Result } from '@/lib/types';
import { initialState, reducer } from './linkedin-connect-state';

// Only a provider rejection is about the credentials; a rate limit needs a wait.
function failureKey(code: string) {
  if (code.endsWith('PROVIDER_REJECTED')) return 'rejected';
  if (code.endsWith('RATE_LIMITED')) return 'throttled';
  if (code.endsWith('ALREADY_CONNECTED_ELSEWHERE')) return 'inUse';
  return 'unavailable';
}

// The username field mounts when the credentials step opens. Module-level, so the ref stays stable
// and re-renders don't steal focus back from the password field.
const focusOnMount = (el: HTMLInputElement | null) => el?.focus();

export function LinkedInConnect({ riskNotice }: { riskNotice: string }) {
  const t = useTranslations('integrations.linkedin');
  const router = useRouter();
  const ids = { risk: useId(), user: useId(), pass: useId(), code: useId() };
  const [state, dispatch] = useReducer(reducer, initialState);
  const { pending, error, run: act, reset } = useAction();

  function run(call: () => Promise<Result<ConnectOutcome>>) {
    act(call, {
      onSuccess: (outcome) => {
        if (outcome.kind === 'redirect') window.location.assign(outcome.url);
        else if (outcome.kind === 'checkpoint')
          dispatch({ type: 'checkpointReached', accountId: outcome.accountId });
        else {
          dispatch({ type: 'connected' });
          router.refresh();
        }
      },
      onSettled: () => dispatch({ type: 'secretsCleared' }),
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (state.step === 'checkpoint') {
      const { accountId, code } = state;
      run(() => linkedInCheckpointAction(accountId, code));
    } else if (state.step === 'credentials') {
      const { username, password } = state;
      run(() => connectLinkedInAction(state.accepted, { username, password }));
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="bg-warning-tint flex flex-col gap-2 rounded-md p-3">
        <div className="flex items-start gap-2.5">
          <TriangleAlert aria-hidden="true" className="text-warning size-4 shrink-0" />
          <div className="flex flex-col gap-2">
            <p className="text-ui-strong text-ink">{t('riskTitle')}</p>
            <p className="text-small text-ink whitespace-pre-line">{riskNotice}</p>
          </div>
        </div>
        <div className="flex items-start gap-2.5">
          <Checkbox
            id={ids.risk}
            checked={state.accepted}
            onCheckedChange={(v) => dispatch({ type: 'acceptedChanged', value: v === true })}
            disabled={pending || state.step !== 'idle'}
          />
          <Label htmlFor={ids.risk} className="text-small text-ink leading-snug font-normal">
            {t('riskAccept')}
          </Label>
        </div>
        {state.step === 'idle' && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={!state.accepted || pending}
              onClick={() => run(() => connectLinkedInAction(state.accepted))}
            >
              {pending && <Loader2 className="animate-spin" />}
              {t('connect')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!state.accepted || pending}
              onClick={() => dispatch({ type: 'credentialsOpened' })}
            >
              {t('useCredentials')}
            </Button>
          </div>
        )}
      </div>

      {state.step !== 'idle' && (
        // autoComplete off: not our credential for the browser to remember.
        <form
          onSubmit={submit}
          autoComplete="off"
          className="border-line flex flex-col gap-3.5 rounded-md border p-3"
        >
          {state.step === 'checkpoint' ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={ids.code}>{t('codeLabel')}</Label>
              <Input
                id={ids.code}
                value={state.code}
                onChange={(e) => dispatch({ type: 'codeChanged', value: e.target.value })}
                inputMode="numeric"
                autoComplete="one-time-code"
                disabled={pending}
                required
              />
              <p className="text-small text-muted">{t('codeHint')}</p>
            </div>
          ) : (
            <>
              <p className="text-small text-muted">{t('credentialsPurpose')}</p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.user}>{t('username')}</Label>
                <Input
                  id={ids.user}
                  ref={focusOnMount}
                  type="email"
                  value={state.username}
                  onChange={(e) => dispatch({ type: 'usernameChanged', value: e.target.value })}
                  autoComplete="off"
                  disabled={pending}
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.pass}>{t('password')}</Label>
                <Input
                  id={ids.pass}
                  type="password"
                  value={state.password}
                  onChange={(e) => dispatch({ type: 'passwordChanged', value: e.target.value })}
                  autoComplete="off"
                  disabled={pending}
                  required
                />
              </div>
            </>
          )}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={pending}>
              {pending && <Loader2 className="animate-spin" />}
              {state.step === 'checkpoint' ? t('verify') : t('connect')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={pending}
              onClick={() => {
                dispatch({ type: 'cancelled' });
                reset();
              }}
            >
              {t('cancel')}
            </Button>
          </div>
        </form>
      )}

      {error && (
        <p role="status" className="text-small text-negative">
          {t(`failed.${failureKey(error.code)}`)}
        </p>
      )}
    </div>
  );
}
