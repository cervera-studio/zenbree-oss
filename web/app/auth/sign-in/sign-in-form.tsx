'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';
import { LEGAL } from '@/lib/legal';
import { setStored } from '@/lib/storage/storage';
import { useStored } from '@/lib/storage/use-stored';
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
import { Notice } from '../notice';
// LAST_EMAIL_KEY is only read back on the expired-session screen; never sent by the server.
import { type BanInfo, decodeBan, LAST_EMAIL_KEY, signUpPath } from './params';

export function SignInForm({
  expired,
  ban: signedInBan,
  callbackUrl,
  oauthError,
  canSignUp,
}: {
  expired: boolean;
  ban?: BanInfo | null;
  callbackUrl: string;
  oauthError?: string;
  canSignUp: boolean;
}) {
  const t = useTranslations();
  const errorText = useErrorText();
  const router = useRouter();
  const banned = oauthError === 'BANNED_USER';
  const [error, setError] = useState<string | undefined>(
    oauthError && !banned
      ? t(
          oauthError === 'MICROSOFT_EMAIL_UNVERIFIED'
            ? 'errors.microsoftUnverified'
            : 'errors.generic',
        )
      : undefined,
  );
  // The URL's error_description is attacker-controlled, so a social ban shows the
  // generic notice; reason and expiry only come from the server (the sign-in response or `ban`).
  const [ban, setBan] = useState<BanInfo | undefined>(
    signedInBan ?? (banned ? decodeBan(undefined) : undefined),
  );
  const [pending, setPending] = useState(false);
  const lastEmail = useStored(LAST_EMAIL_KEY, 'local');
  const [typedEmail, setTypedEmail] = useState<string>();
  const email = typedEmail ?? (expired ? lastEmail : null) ?? '';

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const email = String(form.get('email'));
    const password = String(form.get('password'));
    setPending(true);
    setBan(undefined);
    setError(undefined);
    try {
      const { error } = await authClient.signIn.email({
        email,
        password,
        callbackURL: callbackUrl,
      });
      if (error?.code === 'BANNED_USER') return setBan(decodeBan(error.message));
      if (error) return setError(errorText(authError(error)));
    } catch {
      return setError(t('errors.generic'));
    } finally {
      setPending(false);
    }
    setStored(LAST_EMAIL_KEY, email, 'local');
    router.push(callbackUrl);
  }

  return (
    <>
      {ban ? (
        <BanNotice ban={ban} />
      ) : (
        expired && (
          <Notice tone="accent" title={t('auth.expiredTitle')}>
            <p>{t('auth.expiredNotice')}</p>
          </Notice>
        )
      )}
      <SocialButtons callbackUrl={callbackUrl} layout="stack" />
      <EmailDivider />
      <form onSubmit={onSubmit}>
        <FieldGroup className={AUTH_FIELDS}>
          <Field>
            <FieldLabel htmlFor="email">{t('auth.email')}</FieldLabel>
            <TextInput
              value={email}
              onChange={(e) => setTypedEmail(e.target.value)}
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              placeholder={t('auth.emailPlaceholder')}
            />
          </Field>
          <Field>
            <div className="flex items-center justify-between gap-3">
              <FieldLabel htmlFor="password">{t('auth.password')}</FieldLabel>
              <Link href="/auth/reset-password" className={INK_LINK}>
                {t('auth.forgot')}
              </Link>
            </div>
            <PasswordInput id="password" name="password" autoComplete="current-password" />
          </Field>
          <FormError message={error} />
          <Button type="submit" disabled={pending}>
            {t('auth.signIn')}
          </Button>
        </FieldGroup>
      </form>
      {canSignUp && <SwitchLink message="noAccount" href={signUpPath(callbackUrl)} />}
    </>
  );
}

function BanNotice({ ban }: { ban: BanInfo }) {
  const t = useTranslations('auth');
  const format = useFormatter();
  return (
    <Notice tone="negative" title={t('bannedTitle')}>
      <p>
        {ban.expires
          ? t('bannedUntil', {
              date: format.dateTime(new Date(ban.expires), {
                dateStyle: 'long',
                timeStyle: 'short',
              }),
            })
          : t('bannedIndefinite')}
      </p>
      {ban.reason && <p>{t('bannedReason', { reason: ban.reason })}</p>}
      <p>
        {t.rich('bannedSupport', {
          email: LEGAL.contactEmail,
          link: (chunks) => (
            <a href={`mailto:${LEGAL.contactEmail}`} className={INK_LINK}>
              {chunks}
            </a>
          ),
        })}
      </p>
    </Notice>
  );
}
