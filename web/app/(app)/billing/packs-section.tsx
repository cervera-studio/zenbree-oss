import { getLocale, getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import { getPacks } from '@/lib/billing/api';
import { formatEurCents, formatPricePer1000 } from '@/lib/billing/schemas';
import { checkoutAction } from './actions';
import { BillingBlock } from './billing-block';

export async function PacksSection({ profileReady }: { profileReady: boolean }) {
  const [t, locale, packs] = await Promise.all([
    getTranslations('billing'),
    getLocale(),
    getPacks(),
  ]);

  return (
    <BillingBlock title={t('packs.title')}>
      {!packs.success ? (
        <p className="text-muted text-ui">{t('packs.error')}</p>
      ) : packs.data.packs.length === 0 ? (
        <p className="text-muted text-ui">{t('packs.empty')}</p>
      ) : (
        <div className="grid gap-3 desktop:grid-cols-3">
          {packs.data.packs.map((p) => {
            const unitPrice = formatPricePer1000(p.priceEurCents, p.credits, locale);
            return (
              <div
                key={p.id}
                className="border-line bg-ground flex items-center gap-2.5 rounded-md border px-3 py-2.5"
              >
                <div className="flex min-w-0 grow flex-col">
                  <span className="text-data text-ink font-mono tabular-nums">
                    {t('packs.credits', { credits: p.credits })}
                  </span>
                  <span className="text-data-sm text-muted font-mono tabular-nums">
                    {t('packs.price', { price: formatEurCents(p.priceEurCents, locale) })}
                    {unitPrice && ` · ${t('packs.unitPrice', { price: unitPrice })}`}
                  </span>
                </div>
                <form action={checkoutAction}>
                  <input type="hidden" name="packId" value={p.id} />
                  <Button
                    type="submit"
                    variant="outline"
                    size="xs"
                    className="phone:h-control-md"
                    disabled={!profileReady}
                  >
                    {t('packs.buy')}
                  </Button>
                </form>
              </div>
            );
          })}
        </div>
      )}
      <p className="text-muted text-small">{t('vatNote')}</p>
    </BillingBlock>
  );
}
