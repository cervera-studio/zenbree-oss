import { useFormatter, useTranslations } from 'next-intl';
import type { ReportVariant } from '@/lib/reports/api';

/** The counts of a campaign that tests copy B, one group each. Counts with their denominators, no
 * rates: the groups are often small, and a rate reads as a result it is not. */
export function VariantSplit({ variants }: { variants: ReportVariant[] }) {
  const t = useTranslations('reports');
  return (
    <details className="text-muted">
      <summary className="cursor-pointer">{t('variants.summary')}</summary>
      <ul className="flex flex-col gap-2 pb-2">
        {variants.map((variant) => (
          <li key={variant.group}>
            <span className="text-soft">{t(`variants.group.${variant.group}`)}</span>
            <VariantCounts variant={variant} />
          </li>
        ))}
      </ul>
    </details>
  );
}

function VariantCounts({ variant }: { variant: ReportVariant }) {
  const t = useTranslations('reports');
  const format = useFormatter();
  const n = (value: number) => format.number(value, 'credits');
  const { sent } = variant;
  const linkedin = sent.linkedinConnect + sent.linkedinMessage;
  // Unattributed replies come from no person the campaign contacted: a denominator here would read as nonsense (1 of 0).
  const plain = variant.group === 'unknown';
  const measures = [
    ['enrolled', n(variant.enrolled)],
    ['contacted', n(variant.contacted)],
    [
      'sent',
      linkedin > 0
        ? `${n(sent.email + linkedin)} (${t('channels', { email: sent.email, invites: sent.linkedinConnect, messages: sent.linkedinMessage })})`
        : n(sent.email),
    ],
    [
      'replied',
      plain
        ? n(variant.humanReplies)
        : t('variants.repliedOf', { replied: variant.humanReplies, contacted: variant.contacted }),
    ],
    [
      'positive',
      plain
        ? n(variant.positiveReplies)
        : t('variants.positiveOf', {
            positive: variant.positiveReplies,
            replied: variant.humanReplies,
          }),
    ],
  ] as const;
  return (
    <dl className="grid grid-cols-2 gap-x-3">
      {measures.map(([key, value]) => (
        <div key={key} className="contents">
          <dt>{t(`variants.${key}`)}</dt>
          <dd className="text-right">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
