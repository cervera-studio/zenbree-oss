import { getFormatter, getLocale, getTranslations } from 'next-intl/server';
import { KeyValue } from '@/components/admin/key-value';
import { ImpersonateButton } from '@/components/admin/user-actions';
import { Panel } from '@/components/ui/panel';
import type { ChatSessionDetail } from '@/lib/admin/chats';
import { formatMarginPct, moneyFormatters } from '@/lib/admin/format';

/** The side column: what the chat produced and what it cost us. */
export async function Summary({ s }: { s: ChatSessionDetail }) {
  const [t, tAdmin, locale, format] = await Promise.all([
    getTranslations('admin.transcript'),
    getTranslations('admin'),
    getLocale(),
    getFormatter(),
  ]);
  const sum = s.summary;
  const { eur, usd } = moneyFormatters(locale);
  const costEur = (sum.llmCostUsd + sum.vendorCostUsd) * sum.eurPerUsd;
  const margin =
    sum.marginEur !== null && sum.revenueEur !== null
      ? {
          value: `${eur(sum.marginEur)} · ${formatMarginPct(sum.marginEur, sum.revenueEur, locale)}`,
          tone: sum.marginEur < 0 ? ('negative' as const) : ('positive' as const),
        }
      : null;

  return (
    <aside className="flex shrink-0 flex-col gap-3 desktop:sticky desktop:top-18 desktop:w-75">
      <Panel title={t('summary')}>
        <KeyValue
          items={[
            s.userId
              ? {
                  label: t('summaryUser'),
                  value: s.name || s.email || s.userId,
                  href: `/admin/users/${s.userId}`,
                }
              : { label: t('summaryUser'), value: tAdmin('formerMember') },
            sum.jobId
              ? {
                  label: t('summaryJob'),
                  value: sum.jobId.slice(0, 8),
                  title: sum.jobId,
                  data: true,
                  href: `/admin/jobs?job=${sum.jobId}#job-${sum.jobId}`,
                }
              : { label: t('summaryJob'), value: '—' },
            {
              label: t('outcome'),
              value: t('outcomeValue', { people: sum.people, companies: sum.companies }),
            },
            {
              label: t('creditsLabel'),
              value: t('creditsValue', {
                spent: sum.creditsSpent + sum.creditsReturned,
                returned: sum.creditsReturned,
              }),
            },
            { label: t('net'), value: format.number(sum.creditsSpent, 'credits'), data: true },
          ]}
        />
      </Panel>
      <Panel title={t('ourCost')}>
        <KeyValue
          items={[
            { label: t('llm'), value: usd(sum.llmCostUsd), data: true },
            { label: t('vendors'), value: usd(sum.vendorCostUsd), data: true },
            {
              label: t('revenue'),
              value: sum.revenueEur === null ? '—' : eur(sum.revenueEur),
              data: true,
            },
            ...(margin ? [{ label: t('margin'), data: true, ...margin }] : []),
          ]}
        />
        <p className="text-small text-muted pt-1.5">
          {sum.eurPer1000Credits === null
            ? t('noPurchases', { cost: eur(costEur) })
            : t('revenueNote', { price: eur(sum.eurPer1000Credits) })}
        </p>
      </Panel>
      {s.userId && (
        <ImpersonateButton userId={s.userId} label={t('impersonate')} className="w-full" />
      )}
    </aside>
  );
}
