import { useTranslations } from 'next-intl';

export function ReviewedCount({ reviewed, total }: { reviewed: number; total: number }) {
  const t = useTranslations('campaigns.approvals');
  return t.rich('reviewed', {
    reviewed,
    total,
    b: (chunks) => <strong className="text-ink">{chunks}</strong>,
  });
}
