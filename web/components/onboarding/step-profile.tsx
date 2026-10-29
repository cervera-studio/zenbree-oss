import { Ban, Brain, ChevronRight, ListChecks, type LucideIcon, Plus, User, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { type ReactNode, useId, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { BusinessProfile } from '@/lib/onboarding/schema';
import { regionName } from '@/lib/region-name';
import { cn } from '@/lib/utils';
import { ErrorNote, Eyebrow, PendingIcon, StepIntro } from './step-parts';
import { type Command, LIMITS, type ListKey, type TextKey } from './wizard';

const TEXTS: readonly { key: TextKey; rows: number; minHeight: string }[] = [
  { key: 'proof', rows: 2, minHeight: 'min-h-9' },
  { key: 'summary', rows: 3, minHeight: 'min-h-14' },
  { key: 'solution', rows: 3, minHeight: 'min-h-14' },
];
const GROUPS: readonly { id: 'audience' | 'timing' | 'exclude'; lists: readonly ListKey[] }[] = [
  { id: 'audience', lists: ['personas', 'painPoints'] },
  { id: 'timing', lists: ['buyingTriggers', 'markets'] },
  { id: 'exclude', lists: ['exclusions'] },
];
const USAGE: readonly { id: 'segments' | 'list' | 'exclusions' | 'memory'; icon: LucideIcon }[] = [
  { id: 'segments', icon: User },
  { id: 'list', icon: ListChecks },
  { id: 'exclusions', icon: Ban },
  { id: 'memory', icon: Brain },
];

export function ProfileStep({
  profile,
  busy,
  error,
  onChange,
  onBack,
  onConfirm,
}: {
  profile: BusinessProfile;
  busy: Command | null;
  error: string | null;
  onChange: (profile: BusinessProfile) => void;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const t = useTranslations('onboarding.profile');
  const locale = useLocale();
  const proposing = busy?.task === 'propose';
  return (
    <div className="flex flex-1 justify-center px-4 pt-8 desktop:px-8 desktop:pt-5">
      <div className="grid w-full max-w-275 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <main className="flex min-w-0 flex-col gap-2.5">
          <StepIntro compact title={t('title')} lede={t('description')} />

          {/* Locked while saving: the saved profile replaces whatever is typed meanwhile. */}
          <fieldset
            disabled={busy !== null}
            className="m-0 flex min-w-0 flex-col gap-2.5 border-0 p-0"
          >
            <Card labelledBy="profile-identity" title={t('groups.identity')}>
              <div className="grid items-start gap-2 desktop:grid-cols-2 desktop:gap-x-4">
                <Field className="gap-1.5">
                  <FieldLabel htmlFor="profile-companyName" className="text-ui-strong text-ink">
                    {t('sections.companyName')}
                  </FieldLabel>
                  <Input
                    id="profile-companyName"
                    value={profile.companyName}
                    maxLength={LIMITS.line}
                    onChange={(e) => onChange({ ...profile, companyName: e.target.value })}
                  />
                </Field>
                {TEXTS.map(({ key, rows, minHeight }) => (
                  <Field key={key} className="gap-1.5">
                    <FieldLabel htmlFor={`profile-${key}`} className="text-ui-strong text-ink">
                      {t(`sections.${key}`)}
                    </FieldLabel>
                    <Textarea
                      id={`profile-${key}`}
                      rows={rows}
                      maxLength={LIMITS.paragraph}
                      value={profile[key]}
                      placeholder={key === 'proof' ? t('proofPlaceholder') : undefined}
                      onChange={(e) => onChange({ ...profile, [key]: e.target.value })}
                      className={cn(
                        'field-sizing-content resize-y border-line-strong bg-ground px-2.5 py-2 text-ui shadow-none md:text-ui',
                        minHeight,
                      )}
                    />
                    {key === 'proof' && !profile.proof.trim() && (
                      <FieldDescription className="text-small text-warning">
                        {t('proofMissing')}
                      </FieldDescription>
                    )}
                  </Field>
                ))}
              </div>
            </Card>

            {GROUPS.map((group) => (
              <Card
                key={group.id}
                labelledBy={`profile-${group.id}`}
                title={t(`groups.${group.id}`)}
              >
                <div className="grid gap-4 desktop:grid-cols-2">
                  {group.lists.map((key) => (
                    <ListField
                      key={key}
                      label={t(`sections.${key}`)}
                      hint={t(`hints.${key}`)}
                      negative={key === 'exclusions'}
                      showUntilEdited={key === 'markets' ? (v) => regionName(v, locale) : undefined}
                      items={profile[key]}
                      onChange={(items) => onChange({ ...profile, [key]: items })}
                    />
                  ))}
                </div>
              </Card>
            ))}
          </fieldset>

          <div className="sticky bottom-0 z-10 flex flex-col gap-3 border-t border-line bg-ground py-4">
            <ErrorNote message={error} />
            <div className="flex items-center justify-between gap-3">
              <Button type="button" variant="outline" disabled={busy !== null} onClick={onBack}>
                {t('back')}
              </Button>
              <Button type="button" disabled={busy !== null} onClick={onConfirm}>
                {proposing ? t('proposing') : t('confirm')}
                <PendingIcon pending={proposing} icon={ChevronRight} />
              </Button>
            </div>
          </div>
        </main>

        <aside
          aria-labelledby="profile-usage"
          className="mb-8 flex flex-col gap-3 rounded-lg border border-line bg-raised p-5 lg:sticky lg:top-6"
        >
          <h2 id="profile-usage" className="text-ui-strong text-ink">
            {t('usage.title')}
          </h2>
          <ul className="flex flex-col gap-2.5 text-small text-ink">
            {USAGE.map(({ id, icon: Icon }) => (
              <li key={id} className="flex items-start gap-2.5">
                <Icon
                  aria-hidden="true"
                  className={cn(
                    'size-3.75 shrink-0',
                    id === 'exclusions' ? 'text-negative' : 'text-accent-ink',
                  )}
                />
                <span>
                  <strong>{t(`usage.${id}.title`)}</strong> {t(`usage.${id}.body`)}
                </span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

function Card({
  labelledBy,
  title,
  children,
}: {
  labelledBy: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={labelledBy}
      className="flex flex-col gap-2 rounded-lg border border-line bg-ground p-3"
    >
      <Eyebrow as="h2" id={labelledBy}>
        {title}
      </Eyebrow>
      {children}
    </section>
  );
}

/** One editable row per item, capped at the API's limit; blank rows are dropped on save. */
function ListField({
  label,
  hint,
  negative,
  showUntilEdited,
  items,
  onChange,
}: {
  label: string;
  hint: string;
  negative: boolean;
  showUntilEdited?: (item: string) => string;
  items: string[];
  onChange: (items: string[]) => void;
}) {
  const t = useTranslations('onboarding.profile');
  const labelId = useId();
  const focusIndex = useRef<number | null>(null);
  const [edited, setEdited] = useState<readonly boolean[]>([]);
  const shown = items.map((item, i) =>
    showUntilEdited && !edited[i] ? showUntilEdited(item) : item,
  );

  function add() {
    focusIndex.current = items.length;
    onChange([...items, '']);
  }

  return (
    <Field aria-labelledby={labelId} className="gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span id={labelId} className={cn('text-ui-strong text-ink', negative && 'text-negative')}>
          {label}
        </span>
        <span className="font-mono text-data-sm text-muted tabular-nums">
          {t('count', { count: items.length, max: LIMITS.items })}
        </span>
      </div>
      <div className="flex flex-col gap-1 rounded-md bg-raised p-1">
        {shown.map((value, i) => (
          <div
            key={i}
            className="flex items-center rounded-sm border border-line bg-ground focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/50 hover:border-line-strong"
          >
            <Textarea
              ref={(el) => {
                if (el && focusIndex.current === i) {
                  focusIndex.current = null;
                  el.focus();
                }
              }}
              aria-label={t('item', { label, n: i + 1 })}
              rows={1}
              value={value}
              maxLength={LIMITS.line}
              onChange={(e) => {
                setEdited((rows) => {
                  const next = [...rows];
                  next[i] = true;
                  return next;
                });
                onChange(items.map((v, j) => (j === i ? e.target.value.replace(/\n/g, ' ') : v)));
              }}
              onKeyDown={(e) =>
                e.key === 'Enter' && !e.nativeEvent.isComposing && e.preventDefault()
              }
              className="field-sizing-content min-h-7 resize-none rounded-sm border-0 bg-transparent px-2.5 py-1 text-ui shadow-none focus-visible:ring-0 md:text-ui"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={value.trim() ? t('remove', { item: value }) : t('removeEmpty')}
              onClick={() => {
                setEdited((rows) => rows.filter((_, j) => j !== i));
                onChange(items.filter((_, j) => j !== i));
              }}
              className="text-muted"
            >
              <X />
            </Button>
          </div>
        ))}
        {items.length < LIMITS.items && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={add}
            className="justify-start text-accent-ink hover:text-accent-ink"
          >
            <Plus />
            {t('add')}
          </Button>
        )}
      </div>
      <FieldDescription className="text-small text-muted">{hint}</FieldDescription>
    </Field>
  );
}
