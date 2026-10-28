'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';
import { setStored } from '@/lib/storage/storage';
import {
  AUTH_FIELDS,
  EmailDivider,
  FormError,
  INK_LINK,
  PasswordInput,
  SocialButtons,
  SwitchLink,
  TextInput,
} from '../fields';
import { LAST_EMAIL_KEY, signInPath, verifyPath } from '../sign-in/params';
import { VERIFY_EMAIL_KEY } from '../verify/verify-email';

/** `callbackUrl`: where the verification link and a social sign-up land (#323: back on an invitation). */
export function SignUpForm({ callbackUrl = '/' }: { callbackUrl?: string }) {
  const t = useTranslations();
  const errorText = useErrorText();
  const router = useRouter();
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const email = String(form.get('email'));
    setPending(true);
    setError(undefined);
    try {
      const { error } = await authClient.signUp.email({
        email,
        password: String(form.get('password')),
        name: String(form.get('name')),
        callbackURL: callbackUrl,
      });
      if (error) return setError(errorText(authError(error)));
    } catch {
      return setError(t('errors.generic'));
    } finally {
      setPending(false);
    }
    setStored(VERIFY_EMAIL_KEY, email, 'session');
    // The emailed link opens in a new tab: the set-password page signs this address in (LAST_EMAIL_KEY).
    setStored(LAST_EMAIL_KEY, email, 'local');
    router.push(verifyPath(callbackUrl));
  }

  return (
    <>
      <SocialButtons callbackUrl={callbackUrl} layout="row" />
      <EmailDivider />
      <form onSubmit={onSubmit}>
        <FieldGroup className={AUTH_FIELDS}>
          <Field>
            <FieldLabel htmlFor="name">{t('auth.name')}</FieldLabel>
            <TextInput
              id="name"
              name="name"
              required
              autoComplete="name"
              placeholder={t('auth.namePlaceholder')}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="email">{t('auth.workEmail')}</FieldLabel>
            <TextInput
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              placeholder={t('auth.emailPlaceholder')}
              aria-describedby="email-hint"
            />
            <FieldDescription id="email-hint">{t('auth.workEmailHint')}</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="password">{t('auth.password')}</FieldLabel>
            <PasswordInput
              id="password"
              name="password"
              autoComplete="new-password"
              aria-describedby="password-hint"
            />
            <FieldDescription id="password-hint">{t('auth.passwordHint')}</FieldDescription>
          </Field>
          <FormError message={error} />
          <Button type="submit" disabled={pending}>
            {t('auth.signUp')}
          </Button>
          <FieldDescription className="text-center [&>a]:no-underline">
            {t.rich('auth.consent', {
              terms: (chunks) => (
                <Link href="/terms" className={INK_LINK}>
                  {chunks}
                </Link>
              ),
              privacy: (chunks) => (
                <Link href="/privacy" className={INK_LINK}>
                  {chunks}
                </Link>
              ),
            })}
          </FieldDescription>
        </FieldGroup>
      </form>
      <SwitchLink message="haveAccount" href={signInPath(callbackUrl)} />
    </>
  );
}
