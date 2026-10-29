'use client';

import { Loader2, Lock } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type FormEvent, type ReactNode, useEffect, useState, useTransition } from 'react';
import { GoogleIcon, MicrosoftIcon } from '@/app/auth/fields';
import { IconTile, SettingsItem } from '@/components/settings/settings-card';
import { SettingsSection } from '@/components/settings/settings-section';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/sonner';
import { authClient } from '@/lib/auth-client';

type PasswordStatus = 'idle' | 'saved' | 'error' | 'wrongPassword' | 'noPassword';
type Providers = { social: ('google' | 'microsoft')[]; password: boolean };

const UNKNOWN_PROVIDERS: Providers = { social: [], password: true };

const SOCIAL_ICON = { google: <GoogleIcon />, microsoft: <MicrosoftIcon /> } as const;

function useProviders(): Providers | null {
  const [providers, setProviders] = useState<Providers | null>(null);
  useEffect(() => {
    let live = true;
    authClient
      .listAccounts()
      .then(({ data }): Providers => {
        if (!data) return UNKNOWN_PROVIDERS;
        const ids = new Set(data.map((a) => a.providerId));
        return {
          social: (['google', 'microsoft'] as const).filter((id) => ids.has(id)),
          password: ids.has('credential'),
        };
      })
      .catch((): Providers => UNKNOWN_PROVIDERS)
      .then((next) => {
        if (live) setProviders(next);
      });
    return () => {
      live = false;
    };
  }, []);
  return providers;
}

function ProviderRow({
  icon,
  name,
  email,
  children,
}: {
  icon: ReactNode;
  name: string;
  email: string;
  children: ReactNode;
}) {
  return (
    <SettingsItem>
      <IconTile>{icon}</IconTile>
      <div className="flex min-w-0 flex-1 flex-col">
        <strong className="text-ui-strong">{name}</strong>
        <span className="text-small text-muted truncate">{email}</span>
      </div>
      {children}
    </SettingsItem>
  );
}

function PasswordRow() {
  const t = useTranslations('settings.password');
  const errors = useTranslations('settings.errors');
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<PasswordStatus>('idle');
  const [pending, startTransition] = useTransition();

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setStatus('idle');
    startTransition(async () => {
      const { error } = await authClient.changePassword({
        currentPassword: String(data.get('currentPassword')),
        newPassword: String(data.get('newPassword')),
        revokeOtherSessions: true,
      });
      if (!error) {
        setOpen(false);
        setStatus('saved');
      } else if (error.code === 'INVALID_PASSWORD') setStatus('wrongPassword');
      else if (error.code === 'CREDENTIAL_ACCOUNT_NOT_FOUND') setStatus('noPassword');
      else setStatus('error');
    });
  }

  return (
    <SettingsItem className="flex-col items-stretch">
      <div className="flex flex-wrap items-center gap-3">
        <IconTile>
          <Lock aria-hidden className="size-4" />
        </IconTile>
        <strong className="text-ui-strong grow">{t('title')}</strong>
        <Button
          type="button"
          variant="outline"
          size="xs"
          aria-expanded={open}
          onClick={() => {
            setOpen(!open);
            setStatus('idle');
          }}
        >
          {open ? t('cancel') : t('change')}
        </Button>
      </div>
      {open && (
        <form className="grid grid-cols-1 items-end gap-3" onSubmit={save}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="current-password" className="text-small font-normal text-soft">
              {t('current')}
            </Label>
            <Input
              id="current-password"
              name="currentPassword"
              type="password"
              required
              autoComplete="current-password"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-password" className="text-small font-normal text-soft">
              {t('new')}
            </Label>
            <Input
              id="new-password"
              name="newPassword"
              type="password"
              required
              minLength={8}
              maxLength={128}
              autoComplete="new-password"
            />
          </div>
          <div className="flex flex-col gap-2">
            <p className="text-small text-muted">{t('description')}</p>
            <Button type="submit" disabled={pending} className="self-start">
              {pending && <Loader2 className="size-4 animate-spin" />}
              {t('submit')}
            </Button>
          </div>
        </form>
      )}
      {status === 'saved' && (
        <p role="status" className="text-small text-positive">
          {t('saved')}
        </p>
      )}
      {(status === 'error' || status === 'wrongPassword' || status === 'noPassword') && (
        <p role="alert" className="text-small text-negative">
          {errors(status)}
        </p>
      )}
    </SettingsItem>
  );
}

function SignOutOthers() {
  const t = useTranslations('settings.sessions');
  const [open, setOpen] = useState(false);

  async function revoke() {
    try {
      const { error } = await authClient.revokeOtherSessions();
      if (error) toast.error(t('error'));
      else toast.success(t('done'));
    } catch {
      toast.error(t('error'));
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="link"
        className="h-auto self-start p-0"
        onClick={() => setOpen(true)}
      >
        {t('action')}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('title')}
        description={t('description')}
        confirmLabel={t('confirm')}
        destructive={false}
        onConfirm={revoke}
      />
    </>
  );
}

export function AccessCard({ email }: { email: string }) {
  const t = useTranslations('settings.access');
  const providers = useProviders();

  return (
    <SettingsSection title={t('title')}>
      {providers?.social.map((id) => (
        <ProviderRow key={id} icon={SOCIAL_ICON[id]} name={t(`providers.${id}`)} email={email}>
          <Badge tone="positive">{t('linked')}</Badge>
        </ProviderRow>
      ))}
      {(providers?.password ?? true) && <PasswordRow />}
      <SignOutOthers />
    </SettingsSection>
  );
}
