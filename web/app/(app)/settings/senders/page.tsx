import { Mail, Plus } from 'lucide-react';
import Link from 'next/link';
import { getFormatter, getTranslations } from 'next-intl/server';
import { Suspense } from 'react';
import {
  InstantlyManagedSenders,
  InstantlyOverlap,
} from '@/components/integrations/instantly-senders';
import { SenderAccounts } from '@/components/integrations/sender-accounts';
import { SenderControls } from '@/components/integrations/sender-controls';
import { SenderWindow } from '@/components/integrations/sender-window';
import { LoadError } from '@/components/settings/load-error';
import { IconTile } from '@/components/settings/settings-card';
import { SettingsPageHeader } from '@/components/settings/settings-page-header';
import { SettingsSection } from '@/components/settings/settings-section';
import { Tag, type Tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { getInstantlyAccounts } from '@/lib/integrations/instantly';
import { getSenderAccounts, getSenders } from '@/lib/integrations/mailboxes';
import { getProjects } from '@/lib/projects/server';

type DnsGrade = 'pass' | 'managed' | 'warn' | 'unknown' | 'fail';

const GRADE_TONE: Record<DnsGrade, Tone> = {
  pass: 'positive',
  managed: 'neutral',
  warn: 'warning',
  unknown: 'neutral',
  fail: 'negative',
};

const BOX = 'border-line flex min-w-0 flex-1 flex-col gap-2 rounded-md border p-3';

export default async function SendersPage() {
  // Starts the Instantly read now, beside the native ones; the Suspense children reuse it through React cache.
  void getInstantlyAccounts();
  const [t, tc, format, result, accounts, projects] = await Promise.all([
    getTranslations('senders'),
    getTranslations('campaigns.senderHold'),
    getFormatter(),
    getSenders(),
    getSenderAccounts(),
    getProjects(),
  ]);

  return (
    <div className="flex flex-col gap-4">
      <SettingsPageHeader
        title={t('title')}
        description={t('description')}
        action={
          <Button asChild variant="outline" size="sm">
            <Link href="/settings/integrations">
              <Plus aria-hidden="true" />
              {t('connect')}
            </Link>
          </Button>
        }
      />
      {!result.success && <LoadError message={t('loadError')} />}
      {result.success && result.data.senders.length === 0 && (
        <p className="text-small text-muted">
          {t.rich('empty', {
            link: (chunks) => (
              <Link href="/settings/integrations" className="text-accent-ink underline">
                {chunks}
              </Link>
            ),
          })}
        </p>
      )}
      {result.success &&
        result.data.senders.map((s) => (
          <SettingsSection
            key={s.id}
            title={s.accountLabel ?? s.provider}
            description={t(`providers.${s.provider}`)}
            icon={
              <IconTile>
                <Mail className="size-4" />
              </IconTile>
            }
            action={
              <SenderControls
                senderId={s.id}
                address={s.accountLabel ?? s.provider}
                paused={s.paused}
                state={s.state}
                reason={s.reason}
              />
            }
          >
            <div className="flex flex-col items-stretch gap-3 desktop:flex-row">
              <div className={BOX}>
                <span className="text-ui-strong">
                  {t('usage', { sent: s.sentToday, cap: s.dailyCap })}
                </span>
                {s.dailyCap > 0 && (
                  <div
                    role="progressbar"
                    aria-label={t('usageLabel')}
                    aria-valuemin={0}
                    aria-valuemax={s.dailyCap}
                    aria-valuenow={Math.min(s.sentToday, s.dailyCap)}
                    className="bg-hair h-1 overflow-hidden rounded-xs"
                  >
                    {/* Width from data: a style, not a class, since it is any of 101 values. */}
                    <div
                      className="bg-accent h-1 rounded-xs"
                      style={{ width: `${Math.min(100, (s.sentToday / s.dailyCap) * 100)}%` }}
                    />
                  </div>
                )}
                {s.reason && (
                  <span className="text-small text-soft">{t(`reasonHelp.${s.reason}`)}</span>
                )}
                {s.resumesAt && (
                  <span className="text-small text-soft">
                    {t('resumes', {
                      when: format.dateTime(new Date(s.resumesAt), {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      }),
                    })}
                  </span>
                )}
                {s.warmingUp && <span className="text-small text-soft">{t('rampHelp')}</span>}
                {s.held > 0 && (
                  <span className="text-small text-soft">{tc('count', { count: s.held })}</span>
                )}
                {s.accountLabel && (
                  <Suspense fallback={null}>
                    <InstantlyOverlap address={s.accountLabel} />
                  </Suspense>
                )}
              </div>
              <div className={BOX}>
                {s.dns ? (
                  <>
                    <span className="text-ui-strong">
                      {t('domain', { domain: s.domain ?? '' })}
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {(['spf', 'dkim', 'dmarc'] as const).map((record) => {
                        const grade = s.dns?.[record] ?? 'unknown';
                        return (
                          <Tag key={record} tone={GRADE_TONE[grade]}>
                            {record.toUpperCase()} {t(`grade.${grade}`)}
                          </Tag>
                        );
                      })}
                    </div>
                  </>
                ) : (
                  <p className="text-small text-muted">{t('noDomain')}</p>
                )}
              </div>
            </div>
            <SenderWindow
              senderId={s.id}
              startHour={s.sendWindowStartHour}
              endHour={s.sendWindowEndHour}
              days={s.sendWindowDays}
              timeZone={s.sendTimezone}
            />
          </SettingsSection>
        ))}
      <p className="text-small text-muted max-w-225">{t('dnsHelp')}</p>
      <Suspense fallback={null}>
        <InstantlyManagedSenders />
      </Suspense>
      {accounts.success && projects.success && accounts.data.accounts.length > 0 && (
        <SettingsSection title={t('accounts.title')} description={t('accounts.description')}>
          <SenderAccounts
            accounts={accounts.data.accounts}
            clients={projects.data.projects.map(({ id, name }) => ({ id, name }))}
          />
        </SettingsSection>
      )}
    </div>
  );
}
