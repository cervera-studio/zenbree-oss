'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';
import { useStored } from '@/lib/storage/use-stored';
import { AUTH_FIELDS, FormError, INK_LINK, PasswordInput, TextInput } from '../fields';
import { Notice } from '../notice';
import { LAST_EMAIL_KEY, signInPath } from '../sign-in/params';

// Two steps on one page: no `token` → ask for the email; the emailed link
// comes back here with `?token=` → ask for the new password.
export function ResetForm({
  token,
  isNew = false,
  callbackUrl = '/',
}: {
  token: string | undefined;
  isNew?: boolean;
  callbackUrl?: string;
}) {
  const t = useTranslations();
  const router = useRouter();
  const signUpEmail = useStored(LAST_EMAIL_KEY, 'local');
  const errorText = useErrorText();
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const value = String(new FormData(e.currentTarget).get('value'));
    setPending(true);
    setError(undefined);
    try {
      const { error } = token
        ? await authClient.resetPassword({ token, newPassword: value })
        : await authClient.requestPasswordReset({
            email: value,
            redirectTo: '/auth/reset-password',
          });
      if (error) return setError(errorText(authError(error)));
    } catch {
      return setError(t('errors.generic'));
    } finally {
      setPending(false);
    }
    // A new account chose its first password: sign it in with the address it signed up with, here
    // or in the tab that did; with no such address it signs in by hand.
    if (isNew && signUpEmail) {
      const { error: signInError } = await authClient.signIn.email({
        email: signUpEmail,
        password: value,
      });
      if (!signInError) return router.push(callbackUrl);
    }
    setDone(true);
  }

  return (
    <>
      {done ? (
        <Notice tone="positive">
          <p>{t(token ? (isNew ? 'auth.setPasswordDone' : 'auth.resetDone') : 'auth.resetSent')}</p>
        </Notice>
      ) : (
        <form onSubmit={onSubmit}>
          <FieldGroup className={AUTH_FIELDS}>
            {token ? (
              <Field>
                <FieldLabel htmlFor="value">{t('auth.newPassword')}</FieldLabel>
                <PasswordInput
                  id="value"
                  name="value"
                  autoComplete="new-password"
                  aria-describedby="value-hint"
                />
                <FieldDescription id="value-hint">{t('auth.passwordHint')}</FieldDescription>
              </Field>
            ) : (
              <Field>
                <FieldLabel htmlFor="value">{t('auth.email')}</FieldLabel>
                <TextInput
                  id="value"
                  name="value"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder={t('auth.emailPlaceholder')}
                />
              </Field>
            )}
            <FormError message={error} />
            <Button type="submit" disabled={pending}>
              {t(token ? 'auth.resetSave' : 'auth.resetSend')}
            </Button>
          </FieldGroup>
        </form>
      )}
      <Link href={signInPath(callbackUrl)} className={`self-center ${INK_LINK}`}>
        {t('auth.backToSignIn')}
      </Link>
    </>
  );
}
