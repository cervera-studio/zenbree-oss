'use client';

import { EyeIcon, EyeOffIcon } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type ComponentProps, useState } from 'react';
import { Button } from '@/components/ui/button';
import { FieldError, FieldSeparator } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { authClient } from '@/lib/auth-client';
import { cn } from '@/lib/utils';

export const INK_LINK = 'text-ui-strong text-accent-ink hover:underline underline-offset-4';

// The design's field rhythm, set once on the group instead of on every Field and FieldLabel. Label sets its own
// weight and leading, which beat the text-ui-strong fallbacks, so both are repeated here. leading-5 is the stock step nearest to 1.45 on 13.5 px.
export const AUTH_FIELDS =
  'gap-4 [&_[data-slot=field]]:gap-1.5 [&_[data-slot=field-label]]:text-ui-strong [&_[data-slot=field-label]]:font-bold [&_[data-slot=field-label]]:leading-5 [&_[data-slot=field-label]]:text-soft [&_[data-slot=field-description]]:text-small';

// The scroll margin keeps room under a focused field, so the phone keyboard leaves the submit in view.
const FIELD_INPUT = 'scroll-mb-28';

export function TextInput({ className, ...props }: ComponentProps<typeof Input>) {
  return <Input className={cn(FIELD_INPUT, className)} {...props} />;
}

export function PasswordInput(props: Omit<ComponentProps<typeof Input>, 'type'>) {
  const t = useTranslations('auth');
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        type={visible ? 'text' : 'password'}
        className={cn(FIELD_INPUT, 'pr-10')}
        minLength={8}
        required
        {...props}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="absolute top-0.75 right-0.75"
        aria-label={t(visible ? 'hidePassword' : 'showPassword')}
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? <EyeOffIcon aria-hidden /> : <EyeIcon aria-hidden />}
      </Button>
    </div>
  );
}

export function EmailDivider() {
  const t = useTranslations('auth');
  return <FieldSeparator className="my-0 text-small">{t('withEmail')}</FieldSeparator>;
}

const PROVIDERS = {
  google: { name: 'Google', label: 'google', Icon: GoogleIcon },
  microsoft: { name: 'Microsoft', label: 'microsoft', Icon: MicrosoftIcon },
} as const;

type Provider = keyof typeof PROVIDERS;

// Sign-in stacks the full "Continue with …" buttons; sign-up fits both on one row with the bare
// provider name, so its accessible name spells the action out.
export function SocialButtons({
  callbackUrl,
  layout,
}: {
  callbackUrl: string;
  layout: 'stack' | 'row';
}) {
  const t = useTranslations('auth');
  const social = (provider: Provider) =>
    authClient.signIn.social({
      provider,
      callbackURL: callbackUrl,
      errorCallbackURL: '/auth/sign-in',
    });
  return (
    <div className={cn('grid', layout === 'row' ? 'grid-cols-2 gap-2.5' : 'gap-4')}>
      {(Object.keys(PROVIDERS) as Provider[]).map((provider) => {
        const { name, label, Icon } = PROVIDERS[provider];
        return (
          <Button
            key={provider}
            type="button"
            variant="outline"
            aria-label={layout === 'row' ? t('signUpWith', { provider: name }) : undefined}
            onClick={() => social(provider)}
          >
            <Icon />
            {layout === 'row' ? name : t(label)}
          </Button>
        );
      })}
    </div>
  );
}

export function FormError({ message }: { message: string | undefined }) {
  return <FieldError>{message}</FieldError>;
}

export function SwitchLink({
  message,
  href,
}: {
  message: 'noAccount' | 'haveAccount';
  href: string;
}) {
  const t = useTranslations('auth');
  return (
    <p className="text-ui text-center text-soft">
      {t.rich(message, {
        link: (chunks) => (
          <Link href={href} className={INK_LINK}>
            {chunks}
          </Link>
        ),
      })}
    </p>
  );
}

export function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-4.5">
      <path
        d="M21.6 12.2c0-.7-.1-1.4-.2-2H12v3.8h5.4a4.6 4.6 0 0 1-2 3v2.5h3.2c1.9-1.7 3-4.3 3-7.3z"
        fill="#4285F4"
      />
      <path
        d="M12 22c2.7 0 5-.9 6.6-2.5l-3.2-2.5c-.9.6-2 1-3.4 1-2.6 0-4.8-1.8-5.6-4.1H3.1v2.6A10 10 0 0 0 12 22z"
        fill="#34A853"
      />
      <path d="M6.4 13.9a6 6 0 0 1 0-3.8V7.5H3.1a10 10 0 0 0 0 9z" fill="#FBBC05" />
      <path
        d="M12 6c1.5 0 2.8.5 3.8 1.5l2.9-2.9A10 10 0 0 0 3.1 7.5l3.3 2.6C7.2 7.8 9.4 6 12 6z"
        fill="#EA4335"
      />
    </svg>
  );
}

export function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-4">
      <rect x="2" y="2" width="9.5" height="9.5" fill="#F25022" />
      <rect x="12.5" y="2" width="9.5" height="9.5" fill="#7FBA00" />
      <rect x="2" y="12.5" width="9.5" height="9.5" fill="#00A4EF" />
      <rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#FFB900" />
    </svg>
  );
}
