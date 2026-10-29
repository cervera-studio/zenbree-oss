import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { AdminPage, sectionCrumbs } from '@/components/admin/admin-page';
import { ApiError, Empty } from '@/components/admin/api-error';
import { AddSetting, SettingSwitch } from '@/components/admin/settings-form';
import { Badge, Tag } from '@/components/ui/badge';
import { getSettings } from '@/lib/admin/api';
import { formatDate } from '@/lib/admin/format';
import { cn } from '@/lib/utils';

const DESCRIBED = [
  'vendor_disabled_apollo',
  'vendor_disabled_hunter',
  'vendor_disabled_pdl',
  'vendor_disabled_treg',
  'vendor_disabled_aiark',
  'vendor_disabled_firecrawl',
  'vendor_disabled_google',
  'vendor_disabled_microsoft',
  'vendor_disabled_serper',
  'vendor_disabled_exa',
  'vendor_disabled_millionverifier',
  'vendor_disabled_hubspot',
  'vendor_disabled_instantly',
  'vendor_disabled_unipile',
  'channel_disabled_email',
  'channel_disabled_linkedin',
  'signup_bonus_credits',
  'segments_evaluation_paused',
] as const;

export default async function SettingsPage() {
  const [t, ta, locale, timeZone, format, res] = await Promise.all([
    getTranslations('admin.settings'),
    getTranslations('admin'),
    getLocale(),
    getTimeZone(),
    getFormatter(),
    getSettings(),
  ]);
  // Failed calls in the last 24 hours, shown on the vendor's switch; a vendor without one is listed above.
  const errors = new Map(res.success ? res.data.vendorErrors.map((e) => [e.vendor, e.errors]) : []);
  const switched = new Set(res.success ? res.data.settings.map((s) => s.key) : []);
  const unswitched = [...errors].filter(([v]) => !switched.has(`vendor_disabled:${v}`));

  return (
    <AdminPage
      crumbs={sectionCrumbs(ta('shell.label'), ta('nav.settings'))}
      title={t('title')}
      meta={t('subtitle')}
      className="max-w-257"
    >
      {!res.success ? (
        <ApiError error={res.error} request="GET /v1/admin/settings" retryHref="/admin/settings" />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-ui-strong grow">{t('switches')}</h2>
              <span className="text-small text-muted">{t('onMeans')}</span>
            </div>
            <p className="text-ui text-muted">{t('hint')}</p>
            {unswitched.length > 0 && (
              <p
                role="note"
                className="bg-negative-tint text-negative text-ui rounded-md px-3 py-2.5"
              >
                {t('vendorErrorsNoSwitch', {
                  vendors: unswitched
                    .map(([v, n]) => `${v} (${format.number(n, 'credits')})`)
                    .join(', '),
                })}
              </p>
            )}
            {res.data.settings.length === 0 ? (
              <Empty />
            ) : (
              <ul className="border-line bg-ground divide-hair flex flex-col divide-y overflow-hidden rounded-lg border">
                {res.data.settings.map((s) => {
                  // `vendor_disabled:apollo` → `describe.vendor_disabled_apollo`, when we have one.
                  const described = DESCRIBED.find((k) => k === s.key.replace(':', '_'));
                  const meta = [
                    described && t(`describe.${described}`),
                    s.updatedAt &&
                      (s.updatedByName
                        ? t('updatedAtBy', {
                            date: formatDate(s.updatedAt, { locale, timeZone }),
                            who: s.updatedByName,
                          })
                        : t('updatedAt', { date: formatDate(s.updatedAt, { locale, timeZone }) })),
                  ].filter(Boolean);
                  const failed = s.key.startsWith('vendor_disabled:')
                    ? errors.get(s.key.slice('vendor_disabled:'.length))
                    : undefined;
                  return (
                    <li
                      key={s.key}
                      id={s.key}
                      className={cn(
                        'flex min-h-14 scroll-mt-20 items-center gap-3 px-3 py-2',
                        failed && 'bg-negative-tint',
                      )}
                    >
                      <div className="flex min-w-0 grow flex-col gap-0.5">
                        <span className="flex min-w-0 items-center gap-2">
                          <code
                            title={s.key}
                            className="text-data truncate font-mono font-bold text-ink"
                          >
                            {s.key}
                          </code>
                          {s.value === true && <Tag tone="warning">{t('blocked')}</Tag>}
                          {failed && (
                            <Badge tone="negative">{t('vendorErrors', { n: failed })}</Badge>
                          )}
                        </span>
                        <span className="text-small text-muted">{meta.join(' · ')}</span>
                      </div>
                      {typeof s.value === 'boolean' ? (
                        <SettingSwitch settingKey={s.key} value={s.value} />
                      ) : (
                        <span className="flex shrink-0 items-center gap-3">
                          <Tag tone="neutral">{JSON.stringify(s.value)}</Tag>
                          <span className="text-small text-muted">{t('readOnly')}</span>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          <section className="border-line bg-raised flex flex-col gap-2.5 rounded-lg border p-4">
            <h2 className="text-ui-strong">{t('addTitle')}</h2>
            <AddSetting />
            <p className="text-small text-muted">{t('addHint')}</p>
          </section>
        </>
      )}
    </AdminPage>
  );
}
