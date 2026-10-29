'use client';

import { TriangleAlert } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId } from 'react';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import { ORDER, SENIORITIES, type StepKind } from '@/lib/automations/definition';
import type { StepDrafts } from '@/lib/automations/draft';
import type { Choices } from '@/lib/automations/editor-api';
import { useInstantlyStatus } from '../labels';
import { ChoiceField, Labelled, NumberField } from './fields';

export type SetStep = <K extends StepKind>(kind: K, patch: Partial<StepDrafts[K]>) => void;

type Props = {
  steps: StepDrafts;
  choices: Choices;
  /** Runs spend and write as the creator: their HubSpot is the one written to. */
  viewerIsCreator: boolean;
  setStep: SetStep;
};

export function StepFields(props: Props) {
  const t = useTranslations('automationEditor.steps');
  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="text-ui-strong">{t('title')}</legend>
      <p className="text-small text-muted -mt-1.5">{t('hint')}</p>
      <ol className="flex flex-col gap-3">
        {ORDER.map((kind, i) => (
          <StepCard key={kind} kind={kind} position={i + 1} {...props} />
        ))}
      </ol>
    </fieldset>
  );
}

function StepCard({ kind, position, ...props }: Props & { kind: StepKind; position: number }) {
  const t = useTranslations('automationEditor.steps');
  const tk = useTranslations('automations.step');
  const titleId = useId();
  const switchId = useId();
  const on = props.steps[kind].on;
  return (
    <li className="border-line bg-ground flex flex-col gap-4 rounded-lg border p-4 phone:p-3">
      <div className="flex items-start gap-3">
        <span className="text-small text-muted w-5 shrink-0 tabular-nums">{position}</span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span id={titleId} className="text-ui-strong">
            {tk(`kind.${kind}`)}
          </span>
          <p className="text-small text-muted">{t(`${kind}.hint`)}</p>
          {kind === 'write_hubspot' && !props.viewerIsCreator && (
            <p className="text-small text-muted">{t('write_hubspot.creator')}</p>
          )}
        </div>
        {/* The house switch is 36x20, so on a phone this label is its 44 px target. */}
        <label
          htmlFor={switchId}
          className="mt-0.5 flex shrink-0 phone:-mx-1 phone:-my-3 phone:min-h-11 phone:min-w-11 phone:items-center phone:justify-center"
        >
          <Switch
            id={switchId}
            aria-labelledby={titleId}
            checked={on}
            onCheckedChange={(checked) => props.setStep(kind, { on: checked })}
          />
        </label>
      </div>
      {on && kind === 'find_people' && <FindPeopleFields {...props} />}
      {on && kind === 'qualify' && <QualifyFields {...props} />}
      {on && kind === 'add_to_campaign' && <CampaignFields {...props} />}
      {on && kind === 'add_to_instantly' && <InstantlyFields {...props} />}
    </li>
  );
}

function FindPeopleFields({ steps, choices, setStep }: Props) {
  const t = useTranslations('automationEditor.steps.find_people');
  const ts = useTranslations('automationEditor.seniority');
  const step = steps.find_people;
  const toggle = (value: string, checked: boolean) =>
    setStep('find_people', {
      seniorities: checked
        ? [...step.seniorities, value]
        : step.seniorities.filter((s) => s !== value),
    });
  return (
    <div className="flex flex-col gap-4 pl-8 phone:pl-0">
      <ChoiceField
        label={t('accounts')}
        hint={t('accountsHint')}
        value={step.accountsListId}
        choices={choices.companies}
        onChange={(accountsListId) => setStep('find_people', { accountsListId })}
      />
      <Labelled label={t('titles')} hint={t('titlesHint')}>
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            rows={3}
            value={step.titlesText}
            onChange={(e) => setStep('find_people', { titlesText: e.target.value })}
          />
        )}
      </Labelled>
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">{t('seniorities')}</legend>
        <p className="text-small text-muted">{t('senioritiesHint')}</p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 desktop:grid-cols-3">
          {SENIORITIES.map((value) => (
            <SeniorityBox
              key={value}
              label={ts(value)}
              checked={step.seniorities.includes(value)}
              onChange={(checked) => toggle(value, checked)}
            />
          ))}
        </div>
      </fieldset>
      <NumberField
        label={t('perAccount')}
        hint={t('perAccountHint')}
        value={step.perAccount}
        min={1}
        max={5}
        onChange={(perAccount) => setStep('find_people', { perAccount })}
      />
    </div>
  );
}

function SeniorityBox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex min-h-8 items-center gap-2 phone:min-h-11">
      <Checkbox id={id} checked={checked} onCheckedChange={(next) => onChange(next === true)} />
      <label htmlFor={id} className="text-ui flex flex-1 items-center self-stretch">
        {label}
      </label>
    </div>
  );
}

function QualifyFields({ steps, setStep }: Props) {
  const t = useTranslations('automationEditor.steps.qualify');
  const step = steps.qualify;
  return (
    <div className="flex flex-col gap-4 pl-8 phone:pl-0">
      <Labelled label={t('brief')} hint={t('briefHint')}>
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            aria-describedby={describedBy}
            rows={4}
            maxLength={1000}
            value={step.brief}
            onChange={(e) => setStep('qualify', { brief: e.target.value })}
          />
        )}
      </Labelled>
      <NumberField
        label={t('minScore')}
        hint={t('minScoreHint')}
        value={step.minScore}
        min={0}
        max={100}
        onChange={(minScore) => setStep('qualify', { minScore })}
      />
    </div>
  );
}

function CampaignFields({ steps, choices, setStep }: Props) {
  const t = useTranslations('automationEditor.steps.add_to_campaign');
  const tc = useTranslations('campaigns.status');
  return (
    <div className="flex flex-col gap-4 pl-8 phone:pl-0">
      <ChoiceField
        label={t('campaign')}
        hint={t('campaignHint')}
        value={steps.add_to_campaign.campaignId}
        choices={choices.campaigns.map((c) => ({ id: c.id, name: `${c.name} (${tc(c.status)})` }))}
        onChange={(campaignId) => setStep('add_to_campaign', { campaignId })}
      />
    </div>
  );
}

function InstantlyFields({ steps, choices, setStep }: Props) {
  const t = useTranslations('automationEditor.steps.add_to_instantly');
  const tl = useTranslations('lists.instantly');
  const errorText = useErrorText();
  const statusText = useInstantlyStatus();
  const instantly = choices.instantly;
  const campaignId = steps.add_to_instantly.campaignId;
  if (instantly.state !== 'ready') {
    return (
      <div className="flex flex-col items-start gap-1.5 pl-8 phone:pl-0">
        <p className="text-ui">
          {instantly.state === 'notConnected'
            ? t('notConnected')
            : errorText({ code: instantly.code }, t('unreadable'))}
        </p>
        {campaignId && <p className="text-small text-muted">{t('keepsSaved')}</p>}
        <Link
          href="/settings/integrations"
          className="text-accent-ink text-ui-strong hover:underline phone:flex phone:min-h-11 phone:items-center"
        >
          {t('settings')}
        </Link>
      </div>
    );
  }
  const picked = instantly.campaigns.find((c) => c.id === campaignId);
  return (
    <div className="flex flex-col gap-4 pl-8 phone:pl-0">
      <ChoiceField
        label={t('campaign')}
        hint={instantly.campaigns.length > 0 ? t('campaignHint') : tl('noCampaigns')}
        value={campaignId}
        choices={instantly.campaigns.map((c) => ({
          id: c.id,
          name: `${c.name} (${statusText(c.status)})`,
        }))}
        onChange={(id) => setStep('add_to_instantly', { campaignId: id })}
      />
      {picked?.maySend && (
        <p className="bg-warning-tint text-warning text-small flex gap-2 rounded-md p-3">
          <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          {t('sends')}
        </p>
      )}
    </div>
  );
}
