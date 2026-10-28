'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useTheme } from 'next-themes';
import { type FormEvent, useState, useSyncExternalStore, useTransition } from 'react';
import { SettingsCard } from '@/components/settings/settings-card';
import { SettingRow, SettingsSection } from '@/components/settings/settings-section';
import { Tag } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { LOCALE_NAMES, LOCALES, type Locale, storedLocale } from '@/i18n/config';
import { authClient } from '@/lib/auth-client';
import { AccessCard } from './access-card';
import { setCompanyNameAction, setLocaleAction } from './actions';
import { DeleteAccount } from './delete-account';
import { EmailChange } from './email-change';
import { PhotoControl } from './photo-control';
import { TimeZoneField } from './time-zone-field';

const noop = () => () => {};
const LABEL = 'text-small font-normal text-soft';
const THEMES = ['light', 'dark', 'system'] as const;
type Theme = (typeof THEMES)[number];

function isTheme(value: string | undefined): value is Theme {
  return THEMES.some((theme) => theme === value);
}

function AccountHeader({
  name,
  email,
  image,
  company,
  admin,
}: {
  name: string;
  email: string;
  image: string | null;
  company: string | null;
  admin: boolean;
}) {
  const t = useTranslations('settings.account');
  return (
    <section aria-label={t('headerLabel')}>
      <SettingsCard className="flex-row flex-wrap items-center gap-x-3.5 gap-y-2">
        <PhotoControl name={name} email={email} image={image}>
          <div className="flex min-w-0 flex-1 flex-col gap-px">
            <span className="text-ui-strong truncate">{name || email}</span>
            <span className="text-small text-muted truncate">
              {[email, company].filter(Boolean).join(' · ')}
            </span>
          </div>
          {admin && <Tag tone="accent">{t('admin')}</Tag>}
        </PhotoControl>
      </SettingsCard>
    </section>
  );
}

function PersonalData({
  name: initialName,
  email,
  emailVerified,
  company: initialCompany,
}: {
  name: string;
  email: string;
  emailVerified: boolean;
  company: string | null;
}) {
  const t = useTranslations('settings.account');
  const errors = useTranslations('settings.errors');
  const router = useRouter();
  const [lastSaved, setLastSaved] = useState({ name: initialName, company: initialCompany });
  const { name: savedName, company: savedCompany } = lastSaved;
  const [name, setName] = useState(initialName);
  const [company, setCompany] = useState(initialCompany ?? '');
  const [status, setStatus] = useState<'idle' | 'saved' | 'error'>('idle');
  const [pending, startTransition] = useTransition();

  const nameChanged = name.trim() !== savedName;
  const companyChanged = savedCompany !== null && company.trim() !== savedCompany;
  const dirty = nameChanged || companyChanged;
  const valid = name.trim() !== '' && (!companyChanged || company.trim() !== '');

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus('idle');
    startTransition(async () => {
      try {
        const results = await Promise.all([
          nameChanged
            ? authClient.updateUser({ name: name.trim() }).then(({ error }) => !error)
            : true,
          companyChanged ? setCompanyNameAction(company) : true,
        ]);
        if (results.every(Boolean)) {
          // No business profile: keep `null` so the «Azienda» field stays hidden (#238).
          setLastSaved({
            name: name.trim(),
            company: savedCompany === null ? null : company.trim(),
          });
          setStatus('saved');
        } else setStatus('error');
      } catch {
        setStatus('error');
      }
      router.refresh();
    });
  }

  return (
    <SettingsSection title={t('title')}>
      <form className="flex flex-col gap-3.5" onSubmit={save}>
        <div className="grid grid-cols-1 gap-3 desktop:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="profile-name" className={LABEL}>
              {t('name')}
            </Label>
            <Input
              id="profile-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={100}
              autoComplete="name"
            />
          </div>
          {savedCompany !== null && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="profile-company" className={LABEL}>
                {t('company')}
              </Label>
              <Input
                id="profile-company"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
                required
                maxLength={280}
                autoComplete="organization"
              />
            </div>
          )}
          <div className="flex flex-col gap-1.5 desktop:col-span-2">
            <Label htmlFor="profile-email" className={LABEL}>
              {t('email')}
            </Label>
            <div className="relative">
              <Input
                id="profile-email"
                value={email}
                readOnly
                aria-describedby="profile-email-help"
                className="pr-24"
              />
              {emailVerified && (
                <span className="absolute top-1/2 right-2.5 -translate-y-1/2">
                  <Tag tone="positive">{t('verified')}</Tag>
                </span>
              )}
            </div>
            <span id="profile-email-help" className="text-small text-muted">
              {t('emailHelp')}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" size="sm" disabled={pending || !dirty || !valid}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {t('save')}
          </Button>
          {status === 'error' && (
            <p role="alert" className="text-small text-negative">
              {errors('error')}
            </p>
          )}
          {status !== 'error' && (dirty || status === 'saved') && (
            <p
              role="status"
              className={dirty ? 'text-small text-warning' : 'text-small text-positive'}
            >
              {dirty ? t('unsaved') : t('saved')}
            </p>
          )}
        </div>
      </form>
      <EmailChange email={email} />
    </SettingsSection>
  );
}

function PreferencesCard({
  locale: savedLocale,
  timeZone,
}: {
  locale: Locale;
  timeZone: string | null;
}) {
  const t = useTranslations('settings.preferences');
  const { theme, setTheme } = useTheme();
  const [locale, setLocale] = useState(savedLocale);
  const [pending, startTransition] = useTransition();
  // Client-only values: the server snapshot keeps hydration in sync.
  const mounted = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );

  return (
    <SettingsSection title={t('title')}>
      <div>
        <SettingRow label={t('language')} description={t('languageHelp')}>
          <SegmentedControl
            label={t('language')}
            value={locale}
            onChange={(next) => {
              if (pending) return;
              setLocale(next);
              startTransition(async () => {
                try {
                  await setLocaleAction(next);
                } catch {
                  setLocale(savedLocale);
                }
              });
            }}
            options={LOCALES.map((value) => ({ value, label: LOCALE_NAMES[value] }))}
          />
        </SettingRow>
        <SettingRow label={t('theme')} description={t('themeHelp')}>
          <SegmentedControl
            label={t('theme')}
            value={mounted && isTheme(theme) ? theme : 'system'}
            onChange={setTheme}
            options={THEMES.map((value) => ({ value, label: t(`themes.${value}`) }))}
          />
        </SettingRow>
        <TimeZoneField saved={timeZone} />
      </div>
    </SettingsSection>
  );
}

export function ProfileForm({
  name,
  email,
  image,
  emailVerified,
  company,
  locale,
  timeZone,
  admin = false,
}: {
  name: string;
  email: string;
  image: string | null;
  emailVerified: boolean;
  company: string | null;
  locale: string;
  timeZone: string | null;
  admin?: boolean;
}) {
  return (
    <div className="flex flex-col gap-4 @5xl:flex-row @5xl:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <AccountHeader name={name} email={email} image={image} company={company} admin={admin} />
        <PersonalData name={name} email={email} emailVerified={emailVerified} company={company} />
        <PreferencesCard locale={storedLocale(locale)} timeZone={timeZone} />
      </div>
      <div className="flex flex-col gap-4 @5xl:w-95 @5xl:shrink-0">
        <AccessCard email={email} />
        <DeleteAccount />
      </div>
    </div>
  );
}
