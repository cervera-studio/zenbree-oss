'use client';

import { useTranslations } from 'next-intl';
import { type ReactNode, useId, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { MAX_BODY, type Step, type Variant } from '@/lib/campaigns/steps';
import { cn } from '@/lib/utils';

type Copy = { subject: string; body: string };
type Chips = { token: string; label: string }[];
type Picker = (insert: (token: string) => void) => ReactNode;

/** A step's copy: one editor, or copy A and copy B in two tabs while the campaign tests them (#934). */
export function StepCopyTabs({
  step,
  abTest,
  tab,
  onTab,
  editable,
  onChange,
  chips,
  picker,
}: {
  step: Step;
  abTest: boolean;
  tab: Variant;
  onTab: (tab: Variant) => void;
  editable: boolean;
  onChange: (patch: Partial<Step>) => void;
  chips: Chips;
  picker?: Picker;
}) {
  const t = useTranslations('campaignBuilder.variants');
  const shared = { step, editable, chips, picker };
  const a = <CopyFields {...shared} copy={step} onChange={onChange} />;
  if (!abTest) return a;
  return (
    <Tabs value={tab} onValueChange={(v) => onTab(v as Variant)}>
      <TabsList aria-label={t('tabs')}>
        <TabsTrigger value="a">{t('a')}</TabsTrigger>
        <TabsTrigger value="b">{t('b')}</TabsTrigger>
      </TabsList>
      <TabsContent value="a" className="space-y-3">
        {a}
      </TabsContent>
      <TabsContent value="b" className="space-y-3">
        {step.b ? (
          <>
            <CopyFields
              {...shared}
              copy={step.b}
              onChange={(patch) => onChange({ b: { ...(step.b as Copy), ...patch } })}
            />
            {editable && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChange({ b: undefined })}
              >
                {t('useA')}
              </Button>
            )}
          </>
        ) : (
          <div className="border-line bg-raised flex flex-wrap items-center gap-3 rounded-lg border p-3">
            <p className="text-ui text-muted min-w-0 flex-1">{t('sameAsA')}</p>
            {editable && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onChange({ b: { subject: step.subject, body: step.body } })}
              >
                {t('write')}
              </Button>
            )}
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
}

function CopyFields({
  step,
  copy,
  onChange,
  editable,
  chips,
  picker,
}: {
  step: Step;
  copy: Copy;
  onChange: (patch: Partial<Copy>) => void;
  editable: boolean;
  chips: Chips;
  picker?: Picker;
}) {
  const t = useTranslations('campaigns.step');
  const tb = useTranslations('campaignBuilder.steps');
  const id = useId();
  const email = step.channel === 'email';
  const task = step.channel === 'hubspot_task';
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  // The field a variable chip writes into: the last one focused, else the message.
  const target = useRef<'subject' | 'body'>('body');
  const max = MAX_BODY[step.channel];

  function insert(token: string) {
    const field = target.current === 'subject' && subjectRef.current ? 'subject' : 'body';
    const el = field === 'subject' ? subjectRef.current : bodyRef.current;
    const value = copy[field];
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    onChange({ [field]: value.slice(0, start) + token + value.slice(end) });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  }

  // The fields alone: the copy tabs around them still switch on a read-only step.
  return (
    <fieldset disabled={!editable} className="space-y-3">
      {((email && !step.threaded) || task) && (
        <div className="space-y-1">
          <Label htmlFor={`${id}-subject`} className="text-ui-strong text-ink">
            {task ? t('taskSubject') : t('subject')}
          </Label>
          <Input
            id={`${id}-subject`}
            ref={subjectRef}
            value={copy.subject}
            maxLength={300}
            onFocus={() => {
              target.current = 'subject';
            }}
            onChange={(e) => onChange({ subject: e.target.value })}
          />
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor={`${id}-body`} className="text-ui-strong text-ink">
          {step.channel === 'linkedin_connect' ? t('note') : t('body')}
        </Label>
        <Textarea
          id={`${id}-body`}
          ref={bodyRef}
          value={copy.body}
          rows={email ? 8 : 4}
          className="border-line-strong bg-ground text-ink desktop:text-ui"
          maxLength={max}
          onFocus={() => {
            target.current = 'body';
          }}
          onChange={(e) => onChange({ body: e.target.value })}
        />
      </div>
      <div className="text-small flex flex-wrap items-center gap-1.5">
        {editable && (
          <>
            <span className="text-muted">{tb('variables')}</span>
            {chips.map(({ token, label: name }) => (
              <button
                key={token}
                type="button"
                // Keep the caret where it was: the chip writes at the cursor.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => insert(`{{${token}}}`)}
                aria-label={tb('insert', { token: name })}
                className="bg-raised text-soft text-data-sm hover:bg-hair rounded-sm px-1.5 py-px font-mono"
              >
                {name}
              </button>
            ))}
            {picker?.(insert)}
          </>
        )}
        <span
          className={cn(
            'text-muted text-data-sm ml-auto font-mono tabular-nums',
            copy.body.length >= max && 'text-negative',
          )}
        >
          {tb('counter', { count: copy.body.length, max })}
        </span>
      </div>
    </fieldset>
  );
}
