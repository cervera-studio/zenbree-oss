import { useTranslations } from 'next-intl';
import { type FormEvent, useRef } from 'react';
import { flushSync } from 'react-dom';
import { Button } from '@/components/ui/button';
import { Field, FieldLabel } from '@/components/ui/field';
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from '@/components/ui/input-group';
import { Textarea } from '@/components/ui/textarea';
import type { StudyInput } from '@/lib/onboarding/study';
import { Analysing } from './analysing-panel';
import { ErrorNote, Eyebrow, StepIntro } from './step-parts';
import { type Command, type CompanyDraft, LIMITS, parseCompany, type StudyCommand } from './wizard';

const NEXT = ['profile', 'segments', 'list'] as const;

export function CompanyStep({
  company,
  busy,
  error,
  onChange,
  onSubmit,
  onCancel,
}: {
  company: CompanyDraft;
  busy: Command | null;
  error: string | null;
  onChange: (patch: Partial<CompanyDraft>) => void;
  onSubmit: (input: StudyInput) => void;
  onCancel: () => void;
}) {
  const t = useTranslations('onboarding.company');
  const input = parseCompany(company);
  const website = useRef<HTMLInputElement>(null);
  const description = useRef<HTMLTextAreaElement>(null);

  function cancel(study: StudyCommand) {
    // Rendered now, so the form's fields exist to take the focus the panel's button had.
    flushSync(onCancel);
    (study.input.website ? website : description).current?.focus();
  }

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (input) onSubmit(input);
  }

  return (
    <main className="flex flex-1 justify-center px-4 py-10 desktop:px-8 desktop:pt-12 desktop:pb-16">
      <div className="flex w-full max-w-160 flex-col gap-6">
        <StepIntro title={t('title')} lede={t('description')} />

        {busy?.task === 'study' ? (
          <Analysing study={busy} onCancel={() => cancel(busy)} />
        ) : (
          <form
            onSubmit={submit}
            className="flex flex-col gap-4 rounded-lg border border-line bg-ground p-5"
          >
            <Field className="gap-1.5">
              <FieldLabel htmlFor="onboarding-website" className="text-ui-strong text-ink">
                {t('website')}
              </FieldLabel>
              <InputGroup>
                <InputGroupAddon className="pr-0">
                  <InputGroupText className="text-ui">https://</InputGroupText>
                </InputGroupAddon>
                <InputGroupInput
                  ref={website}
                  id="onboarding-website"
                  value={company.website}
                  onChange={(e) => onChange({ website: e.target.value })}
                  placeholder="acme.com"
                  maxLength={LIMITS.website}
                  inputMode="url"
                  autoComplete="url"
                  className="pl-1!"
                />
              </InputGroup>
            </Field>
            <Field className="gap-1.5">
              <FieldLabel
                htmlFor="onboarding-description"
                className="gap-1 text-ui-strong text-ink"
              >
                {t('about')}
                <span className="text-ui text-muted">{t('aboutHint')}</span>
              </FieldLabel>
              <Textarea
                ref={description}
                id="onboarding-description"
                value={company.description}
                onChange={(e) => onChange({ description: e.target.value })}
                placeholder={t('aboutPlaceholder')}
                maxLength={LIMITS.description}
                rows={4}
                className="min-h-24 resize-y border-line-strong bg-ground px-2.5 py-2 text-ui shadow-none md:text-ui"
              />
            </Field>
            <ErrorNote message={error} />
            <div className="flex flex-wrap items-center justify-end gap-3">
              <span className="min-w-48 flex-1 text-small text-muted">{t('free')}</span>
              <Button type="submit" disabled={!input || busy !== null}>
                {t('submit')}
              </Button>
            </div>
          </form>
        )}

        <ol className="grid gap-4 desktop:grid-cols-3">
          {NEXT.map((id) => (
            <li key={id} className="flex flex-col gap-1 rounded-lg border border-line p-3.5">
              <Eyebrow>{t(`next.${id}.tag`)}</Eyebrow>
              <span className="text-ui-strong text-ink">{t(`next.${id}.title`)}</span>
              <span className="text-small text-soft">{t(`next.${id}.body`)}</span>
            </li>
          ))}
        </ol>
      </div>
    </main>
  );
}
