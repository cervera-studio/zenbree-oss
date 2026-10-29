'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Panel } from '@/components/ui/panel';
import type { CatalogEntry, FieldCatalog } from '@/lib/campaigns/fields';
import { type Channel, MAX_STEPS, type Step, type Variant } from '@/lib/campaigns/steps';
import { AddStepButtons } from './builder-parts';
import { FieldPicker } from './field-picker';
import { SnippetDialog } from './snippet-dialog';
import { StepCard } from './step-card';

export function BuilderSteps({
  steps,
  open,
  abTest,
  draft,
  pending,
  chips,
  catalog,
  snippetCampaignId,
  onToggle,
  onChange,
  onRemove,
  onAdd,
  onPick,
  onTest,
}: {
  steps: Step[];
  open: number | null;
  abTest: boolean;
  draft: boolean;
  pending: boolean;
  chips: { token: string; label: string }[];
  catalog: FieldCatalog | null;
  snippetCampaignId: string | null;
  onToggle: (index: number) => void;
  onChange: (index: number, step: Step) => void;
  onRemove: (index: number, step: Step) => void;
  onAdd: (channel: Channel) => void;
  onPick: (entry: CatalogEntry) => string;
  onTest: (index: number) => ((variant: Variant) => void) | undefined;
}) {
  const tb = useTranslations('campaignBuilder');
  const [snippetOpen, setSnippetOpen] = useState(false);
  return (
    <Panel
      title={tb('steps.title')}
      meta={draft ? tb('steps.meta', { count: steps.length, max: MAX_STEPS }) : tb('steps.locked')}
    >
      <div className="flex flex-col gap-3">
        {steps.length === 0 && <p className="text-muted text-ui">{tb('steps.none')}</p>}
        <ol className="flex flex-col gap-3">
          {steps.map((step, i) => (
            // Steps have no id of their own until saved; their position is what the API keys on.
            <li key={i} className="flex items-start gap-3 phone:gap-2">
              <p className="flex w-11 shrink-0 flex-col items-center pt-3 phone:w-10">
                <span className="text-label text-muted uppercase phone:text-2xs">
                  {tb('steps.day')}
                </span>
                <span className="text-data text-ink font-mono tabular-nums">
                  {step.sendAfterDays}
                </span>
              </p>
              <div className="min-w-0 flex-1">
                <StepCard
                  step={step}
                  number={i + 1}
                  open={open === i}
                  onToggle={() => onToggle(i)}
                  editable={draft}
                  testing={pending}
                  abTest={abTest}
                  onChange={(next) => onChange(i, next)}
                  onRemove={() => onRemove(i, step)}
                  chips={chips}
                  picker={
                    catalog
                      ? (insert) => (
                          <FieldPicker
                            catalog={catalog}
                            onPick={(entry) => insert(`{{${onPick(entry)}}}`)}
                            onAddSnippet={
                              snippetCampaignId ? () => setSnippetOpen(true) : undefined
                            }
                          />
                        )
                      : undefined
                  }
                  onTest={onTest(i)}
                />
              </div>
            </li>
          ))}
        </ol>
        {draft && <AddStepButtons steps={steps} onAdd={onAdd} />}
      </div>
      {snippetCampaignId && (
        <SnippetDialog
          campaignId={snippetCampaignId}
          people={catalog?.people ?? 0}
          open={snippetOpen}
          onOpenChange={setSnippetOpen}
        />
      )}
    </Panel>
  );
}
