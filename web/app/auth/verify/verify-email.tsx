'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { authClient } from '@/lib/auth-client';
import { useStored } from '@/lib/storage/use-stored';
import { FormError, INK_LINK } from '../fields';
import { signUpPath } from '../sign-in/params';

// Written by the sign-up form for this tab only, so the address never travels in the URL.
export const VERIFY_EMAIL_KEY = 'zenbree.verifyEmail';

export function VerifyLede() {
  const t = useTranslations('auth');
  const email = useStored(VERIFY_EMAIL_KEY, 'session');
  if (!email) return <p>{t('verifyBody')}</p>;
  return (
    <p>
      {t.rich('verifySentTo', {
        email,
        address: (chunks) => <strong className="text-ui-strong text-ink">{chunks}</strong>,
      })}
    </p>
  );
}

type Resend = 'idle' | 'sending' | 'sent' | 'failed';

export function VerifyActions({ callbackUrl = '/' }: { callbackUrl?: string }) {
  const t = useTranslations();
  const email = useStored(VERIFY_EMAIL_KEY, 'session');
  const [resend, setResend] = useState<Resend>('idle');

  async function onResend(address: string) {
    setResend('sending');
    const { error } = await authClient.sendVerificationEmail({
      email: address,
      callbackURL: callbackUrl,
    });
    setResend(error ? 'failed' : 'sent');
  }

  return (
    <div className="flex flex-col items-center gap-1.5 text-center text-small text-muted">
      <p role="status">
        {resend === 'sent' ? (
          <span className="font-semibold text-positive">{t('auth.verifyResent')}</span>
        ) : email ? (
          t.rich('auth.verifyResend', {
            resend: (chunks) => (
              <button
                type="button"
                disabled={resend === 'sending'}
                onClick={() => onResend(email)}
                className={`${INK_LINK} rounded-xs disabled:opacity-50`}
              >
                {chunks}
              </button>
            ),
          })
        ) : (
          t('auth.verifyCheckSpam')
        )}
      </p>
      {resend === 'failed' && <FormError message={t('errors.generic')} />}
      <Link href={signUpPath(callbackUrl)} className={INK_LINK}>
        {t('auth.verifyOtherAddress')}
      </Link>
    </div>
  );
}
