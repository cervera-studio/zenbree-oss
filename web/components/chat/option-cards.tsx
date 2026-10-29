'use client';

import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import type { QuestionOption } from '@/lib/chat/types';

export function OptionCards({
  options,
  onPick,
}: {
  options: QuestionOption[];
  onPick: (option: QuestionOption, index: number) => void;
}) {
  const t = useTranslations('toolSummary');
  return (
    <ol className="flex flex-col gap-2">
      {options.map((option, i) => (
        <li key={`${i}-${option.label}`}>
          <button
            type="button"
            onClick={() => onPick(option, i)}
            className="border-line bg-ground hover:bg-raised focus-visible:ring-accent/50 flex w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-3 phone:min-h-11"
          >
            <span className="bg-hair text-soft text-data-sm flex size-5.5 shrink-0 items-center justify-center rounded-sm font-mono tabular-nums">
              {i + 1}
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-px">
              <span className="text-ui-strong">{option.label}</span>
              {option.description && (
                <span className="text-small text-muted">{option.description}</span>
              )}
            </span>
            {option.estimate && (
              <Tag tone="accent">
                {`~${t(`count.${option.estimate.noun}`, { n: option.estimate.count })}`}
              </Tag>
            )}
          </button>
        </li>
      ))}
    </ol>
  );
}
