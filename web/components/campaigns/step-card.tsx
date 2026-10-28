'use client';

import { ChevronDown, Send, Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type ReactNode, useId, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { CHANNELS, type Channel, type Step, type Variant } from '@/lib/campaigns/steps';
import { cn } from '@/lib/utils';
import { StepCopyTabs } from './step-copy-tabs';
import { StepIcon } from './step-icon';

/** C2 (#217): one step of the accordion. Closed it is a one-line summary; open, the editor. */
export function StepCard({
  step,
  number,
  open = true,
  onToggle,
  editable,
  onChange,
  onRemove,
  onTest,
  testing,
  abTest = false,
  chips = [],
  picker,
}: {
  step: Step;
  number: number;
  // Without a toggle (the read-only steps dialog) the step is simply shown open.
  open?: boolean;
  onToggle?: () => void;
  editable: boolean;
  onChange: (step: Step) => void;
  onRemove: () => void;
  // Only for saved email steps: the API renders what is stored, not what is typed.
  onTest?: (variant: Variant) => void;
  testing: boolean;
  // The campaign tests copy B (#934): the copy shows in two tabs.
  abTest?: boolean;
  chips?: { token: string; label: string }[];
  picker?: (insert: (token: string) => void) => ReactNode;
}) {
  const t = useTranslations('campaigns.step');
  const tb = useTranslations('campaignBuilder.steps');
  const id = useId();
  const email = step.channel === 'email';
  const task = step.channel === 'hubspot_task';
  const set = (patch: Partial<Step>) => onChange({ ...step, ...patch });
  const [tab, setTab] = useState<Variant>('a');
  // Cards are keyed by position: once closed, this one may next hold the step that took this place.
  if (!open && tab !== 'a') setTab('a');
  const heading = tb('heading', { number, channel: t(`channels.${step.channel}`) });
  const summary = (step.body || step.subject).replace(/\s+/g, ' ').trim();

  const label = (
    <>
      <StepIcon channel={step.channel} />
      <span className="min-w-0 flex-1">
        <span className="text-ui-strong text-ink block">{heading}</span>
        {!open && (
          <span className="text-muted text-small block truncate font-normal">
            {summary || tb('empty')}
          </span>
        )}
      </span>
      {!open && <ChevronDown className="text-soft size-3.5 shrink-0" aria-hidden="true" />}
    </>
  );

  // One header for both states, so the toggle keeps focus; only the editor below mounts and unmounts.
  return (
    <section
      aria-label={heading}
      className={cn(
        'rounded-lg border',
        open ? 'border-accent bg-ground space-y-3 p-3.5' : 'border-line bg-raised',
      )}
    >
      <div className={cn('flex flex-wrap items-center gap-2', !open && 'p-3')}>
        <h3 className="min-w-0 flex-1">
          {onToggle ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={`${id}-editor`}
              onClick={onToggle}
              className="flex w-full items-center gap-2.5 text-left"
            >
              {label}
            </button>
          ) : (
            <span className="flex items-center gap-2.5">{label}</span>
          )}
        </h3>
        {open && (
          <div className="flex gap-2">
            {onTest && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onTest(abTest ? tab : 'a')}
                disabled={testing}
                className="phone:size-8 phone:has-[>svg]:px-0"
              >
                <Send className="size-4" aria-hidden="true" />
                {/* Icon only on phone: beside the 390 px rail the title wrapped onto three lines. */}
                <span className="phone:sr-only">{t('test')}</span>
              </Button>
            )}
            {editable && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={onRemove}
                aria-label={t('remove')}
              >
                <Trash2 className="size-4" />
              </Button>
            )}
          </div>
        )}
      </div>
      {open && (
        <div id={`${id}-editor`} className="space-y-3">
          <fieldset disabled={!editable} className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor={`${id}-channel`} className="text-ui-strong text-ink">
                {t('channel')}
              </Label>
              <Select
                touch
                id={`${id}-channel`}
                value={step.channel}
                onChange={(e) => set({ channel: e.target.value as Channel })}
              >
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {t(`channels.${c}`)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-28 space-y-1">
              <Label htmlFor={`${id}-delay`} className="text-ui-strong text-ink">
                {tb('day')}
              </Label>
              <Input
                id={`${id}-delay`}
                type="number"
                min={0}
                max={365}
                value={step.sendAfterDays}
                onChange={(e) => set({ sendAfterDays: Number(e.target.value) })}
              />
            </div>
            {email && number > 1 && (
              <div className="flex h-control-md items-center gap-2">
                <Checkbox
                  id={`${id}-threaded`}
                  checked={Boolean(step.threaded)}
                  onCheckedChange={(v) => set({ threaded: v === true })}
                />
                <Label htmlFor={`${id}-threaded`} className="text-ui font-normal">
                  {t('threaded')}
                </Label>
              </div>
            )}
          </fieldset>
          {task && <p className="text-muted text-small">{t('taskHelp')}</p>}
          <StepCopyTabs
            step={step}
            abTest={abTest}
            tab={tab}
            onTab={setTab}
            editable={editable}
            onChange={set}
            chips={chips}
            picker={picker}
          />
        </div>
      )}
    </section>
  );
}
