import { ChevronRight, RotateCw } from 'lucide-react';
import { useFormatter, useLocale, useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { employeeRange, isSeniority } from '@/lib/onboarding/criteria';
import { hasStaleSegments, type Segment } from '@/lib/onboarding/schema';
import { regionName } from '@/lib/region-name';
import { cn } from '@/lib/utils';
import { ErrorNote, Eyebrow, PendingIcon, StepIntro } from './step-parts';
import type { Command } from './wizard';

const CRITERIA = [
  'titles',
  'seniorities',
  'industries',
  'employeeRanges',
  'locations',
  'keywords',
] as const;
const SUMMARY = ['who', 'problem', 'outcome'] as const;

export function SegmentsStep({
  segments,
  busy,
  error,
  onBack,
  onRegenerate,
  onPick,
  onSkip,
}: {
  segments: Segment[];
  busy: Command | null;
  error: string | null;
  onBack: () => void;
  onRegenerate: () => void;
  onPick: (segment: Segment) => void;
  onSkip: () => void;
}) {
  const t = useTranslations('onboarding.segments');
  const locale = useLocale();
  const locked = busy !== null;
  return (
    <main className="flex flex-1 justify-center px-4 py-10 desktop:px-8 desktop:pt-10 desktop:pb-12">
      <div className="flex w-full max-w-300 flex-col gap-6">
        <div className="flex flex-wrap items-end gap-x-2 gap-y-4">
          <StepIntro title={t('title')} lede={t('description')} className="mr-4 min-w-72 flex-1" />
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={locked} onClick={onBack}>
              {t('back')}
            </Button>
            <Button type="button" variant="outline" disabled={locked} onClick={onRegenerate}>
              <PendingIcon pending={busy?.task === 'propose'} icon={RotateCw} />
              {t('regenerate')}
            </Button>
          </div>
        </div>

        <ErrorNote message={error} />

        {hasStaleSegments(segments, locale) && (
          <p className="rounded-lg border border-line bg-raised p-4 text-ui text-soft">
            {t('otherLanguage')}
          </p>
        )}

        {segments.length === 0 ? (
          <p className="rounded-lg border border-line bg-raised p-6 text-center text-ui text-soft">
            {t('empty')}
          </p>
        ) : (
          <div className="grid gap-4 desktop:max-lg:grid-cols-2 lg:grid-cols-3">
            {segments.map((segment, i) => (
              <SegmentCard
                key={segment.name}
                segment={segment}
                featured={i === 0}
                locked={locked}
                picking={busy?.task === 'pick' && busy.segment === segment}
                onPick={() => onPick(segment)}
              />
            ))}
          </div>
        )}

        <p className="text-center text-ui text-soft">
          {t('chatPrompt')}{' '}
          <button
            type="button"
            disabled={locked}
            onClick={onSkip}
            className="text-ui-strong text-accent-ink underline-offset-4 hover:underline disabled:opacity-50"
          >
            {t('chatLink')}
          </button>
        </p>
      </div>
    </main>
  );
}

/** The first card carries the primary action, as in the design; the API does not rank segments. */
function SegmentCard({
  segment,
  featured,
  locked,
  picking,
  onPick,
}: {
  segment: Segment;
  featured: boolean;
  locked: boolean;
  picking: boolean;
  onPick: () => void;
}) {
  const t = useTranslations('onboarding.segments');
  const format = useFormatter();
  const locale = useLocale();
  const criterion = (k: (typeof CRITERIA)[number], v: string) => {
    if (k === 'employeeRanges')
      return employeeRange(v, t('solo'), (n) => format.number(n, 'credits'));
    if (k === 'locations') return regionName(v, locale);
    return k === 'seniorities' && isSeniority(v) ? t(`seniority.${v}`) : v;
  };
  const criteria = CRITERIA.filter((k) => segment.criteria[k].length);
  return (
    <article
      className={cn(
        'flex flex-col gap-3.5 rounded-lg border bg-ground p-5',
        featured ? 'border-accent' : 'border-line',
      )}
    >
      {/* The name comes first in the DOM so a screen reader reads the hint after it; it shows below the hint. */}
      <div className="flex flex-col-reverse gap-2">
        <h2 className="text-feature text-ink">{segment.name}</h2>
        {segment.signalHint && (
          <Badge tone="accent" className="h-auto self-start py-0.5 whitespace-normal">
            {t('signalHint', { signal: segment.signalHint })}
          </Badge>
        )}
      </div>
      <dl className="flex flex-col gap-2.5">
        {SUMMARY.map((k) => (
          <div key={k} className="flex flex-col gap-0.5">
            <dt className="text-small text-muted">{t(k)}</dt>
            <dd className="text-ui text-ink">{segment[k]}</dd>
          </div>
        ))}
      </dl>
      {criteria.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-hair pt-3">
          <Eyebrow>{t('criteriaTitle')}</Eyebrow>
          <ul className="flex flex-wrap gap-1.5">
            {criteria.map((k) => (
              <li key={k} className="rounded-sm bg-raised px-2 py-1 text-small text-ink">
                <span className="text-muted">{t(`criteria.${k}`)}</span>{' '}
                {segment.criteria[k].map((v) => criterion(k, v)).join(', ')}
              </li>
            ))}
          </ul>
        </div>
      )}
      <span className="flex-1" />
      <Button
        type="button"
        variant={featured ? 'primary' : 'outline'}
        className="w-full"
        disabled={locked}
        onClick={onPick}
      >
        {t('pick')}
        <PendingIcon pending={picking} icon={ChevronRight} />
      </Button>
    </article>
  );
}
