'use client';

import { CheckIcon } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type ReactNode, useReducer, useTransition } from 'react';
import { Button } from '@/components/ui/button';
import { requestErasureAction, undoUnsubscribeAction, unsubscribeAction } from './actions';
import { REASONS } from './reasons';
import { initialState, reducer } from './unsubscribe-state';

const strong = (chunks: ReactNode) => <strong className="text-ink">{chunks}</strong>;

export function SenderHeader({ title, detail }: { title: string; detail: string }) {
  const initials = title
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('');
  return (
    <div className="mb-6 flex items-center gap-3">
      <span
        aria-hidden
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-side text-sm font-bold"
      >
        {initials}
      </span>
      <div className="min-w-0">
        <p className="truncate font-semibold">{title}</p>
        {detail && <p className="truncate text-xs-plus text-muted">{detail}</p>}
      </div>
    </div>
  );
}

export function UnsubscribeCard(props: {
  token: string;
  sender: string;
  email: string;
  status: 'subscribed' | 'unsubscribed';
  undoAvailable: boolean;
  lastEmail: string | null;
  unsubscribedOn: string | null;
}) {
  const t = useTranslations('public.unsubscribe');
  const [state, dispatch] = useReducer(reducer, undefined, () =>
    initialState(props.status, props.unsubscribedOn),
  );
  const [pending, startTransition] = useTransition();
  const { sender, email } = props;

  const run = (action: () => Promise<boolean>) =>
    startTransition(async () => dispatch({ type: 'settled', ok: await action() }));

  const unsubscribe = () =>
    run(async () => {
      const res = await unsubscribeAction(props.token, state.reason);
      if (!res.success) return false;
      dispatch({ type: 'unsubscribed', already: res.data.status === 'already_unsubscribed' });
      return true;
    });
  const undo = () =>
    run(async () => {
      const res = await undoUnsubscribeAction(props.token);
      dispatch({ type: res.success ? 'undone' : 'undoRefused' });
      return res.success;
    });
  const erase = () =>
    run(async () => {
      const res = await requestErasureAction(props.token);
      // 429: already sent today, which is what the person wanted.
      dispatch({ type: 'erased', sent: res.success || res.error.status === 429 });
      return true;
    });

  const error = state.failed && <p className="mt-4 text-sm text-negative">{t('failed')}</p>;

  if (state.view === 'ask') {
    return (
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t('askTitle', { sender })}</h1>
        <p className="mt-4 leading-relaxed text-muted">
          {props.lastEmail && <>{props.lastEmail} </>}
          {t.rich('askBody', { email, sender, strong })}
        </p>
        <fieldset className="mt-6">
          <legend className="text-xs-plus font-semibold">
            {t('reason')} <span className="font-normal text-muted">{t('optional')}</span>
          </legend>
          <div className="mt-2">
            {REASONS.map((r) => (
              <label key={r} className="flex min-h-11 cursor-pointer items-center gap-3">
                <input
                  type="radio"
                  name="reason"
                  value={r}
                  checked={state.reason === r}
                  onChange={() => dispatch({ type: 'reasonChosen', reason: r })}
                  className="size-4.5 accent-accent"
                />
                {t(`reasons.${r}`)}
              </label>
            ))}
          </div>
        </fieldset>
        <Button size="lg" className="mt-5 h-12 w-full" onClick={unsubscribe} disabled={pending}>
          {t('confirm')}
        </Button>
        <p className="mt-4 text-center text-xs-plus text-muted">{t('mistake')}</p>
        {error}
      </div>
    );
  }

  if (state.view === 'already') {
    return (
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t('alreadyTitle')}</h1>
        <p className="mt-4 leading-relaxed text-muted">
          {state.since
            ? t.rich('alreadyBody', { date: state.since, email, sender, strong })
            : t.rich('alreadyBodyUndated', { email, sender, strong })}
        </p>
        {error}
      </div>
    );
  }

  return (
    <div>
      <span className="flex size-11 items-center justify-center rounded-full bg-positive/15 text-positive">
        <CheckIcon className="size-5" strokeWidth={2.5} aria-hidden />
      </span>
      <h1 className="mt-4 text-2xl font-bold tracking-tight">{t('doneTitle', { sender })}</h1>
      <p className="mt-4 leading-relaxed text-muted">
        {t.rich('doneBody', { email, sender, strong })}
      </p>
      {state.canUndo && (
        <p className="mt-4 text-sm">
          {t('undoAsk')}{' '}
          <button
            type="button"
            onClick={undo}
            disabled={pending}
            className="font-semibold text-accent-ink underline underline-offset-2 disabled:opacity-50"
          >
            {t('undo')}
          </button>
        </p>
      )}
      <div className="mt-5 rounded-xl bg-side p-4">
        <h2 className="text-sm font-semibold">{t('dataTitle')}</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">{t('dataBody', { sender })}</p>
        {state.erasure === 'idle' && (
          <Button variant="outline" className="mt-3" onClick={erase} disabled={pending}>
            {t('erasure')}
          </Button>
        )}
        {state.erasure === 'sent' && <p className="mt-3 text-sm">{t('erasureSent', { sender })}</p>}
        {state.erasure === 'failed' && (
          <p className="mt-3 text-sm text-negative">{t('erasureFailed', { sender })}</p>
        )}
      </div>
      {error}
    </div>
  );
}
