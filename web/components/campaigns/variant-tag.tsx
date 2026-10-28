import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import type { Variant } from '@/lib/campaigns/steps';

/** The copy a person gets in an A/B test (#934); nothing outside a test or for someone who joined while paused. */
export function VariantTag({ variant }: { variant: Variant | null }) {
  const t = useTranslations('campaignBuilder.variants');
  return variant ? <Tag tone="neutral">{t(variant)}</Tag> : null;
}
