import { getLocale, getTranslations } from 'next-intl/server';
import { SettingsSection } from '@/components/settings/settings-section';
import { StatusNotice } from '@/components/settings/status-notice';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Select } from '@/components/ui/select';
import { getAutoTopup, getPacks } from '@/lib/billing/api';
import { formatEurCents } from '@/lib/billing/schemas';
import { autoTopupAction } from './actions';

// Buys the chosen pack with the saved card whenever the balance drops under the threshold.
export async function AutoTopupSection({ profileReady }: { profileReady: boolean }) {
  const [t, locale, cfg, packs] = await Promise.all([
    getTranslations('billing.autoTopup'),
    getLocale(),
    getAutoTopup(),
    getPacks(),
  ]);
  if (!cfg.success || !packs.success || packs.data.packs.length === 0) return null;
  const { enabled, packId, threshold, thresholds, lastError } = cfg.data;

  return (
    <SettingsSection title={t('title')} description={t('description')}>
      {lastError && <StatusNotice tone="negative">{t('failed', { code: lastError })}</StatusNotice>}
      <form
        action={autoTopupAction}
        className="text-ui flex flex-wrap items-center gap-x-5 gap-y-3"
      >
        <div className="relative flex items-center gap-2">
          <Checkbox
            id="auto-topup-enabled"
            name="enabled"
            defaultChecked={enabled}
            disabled={!enabled && !profileReady}
          />
          <label htmlFor="auto-topup-enabled" className="text-ui-strong">
            {t('enabled')}
          </label>
        </div>
        <label htmlFor="auto-topup-pack" className="flex items-center gap-2">
          {t('pack')}
          <Select touch id="auto-topup-pack" name="packId" defaultValue={packId ?? undefined}>
            {packs.data.packs.map((p) => (
              <option key={p.id} value={p.id}>
                {t('packOption', {
                  credits: p.credits,
                  price: formatEurCents(p.priceEurCents, locale),
                })}
              </option>
            ))}
          </Select>
        </label>
        <label htmlFor="auto-topup-threshold" className="flex items-center gap-2">
          {t('threshold')}
          <Select touch id="auto-topup-threshold" name="threshold" defaultValue={threshold}>
            {thresholds.map((n) => (
              <option key={n} value={n}>
                {t('thresholdOption', { credits: n })}
              </option>
            ))}
          </Select>
        </label>
        <Button type="submit" variant="outline" className="phone:h-11">
          {t('save')}
        </Button>
      </form>
    </SettingsSection>
  );
}
