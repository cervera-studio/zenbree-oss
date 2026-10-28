'use client';

import { useTranslations } from 'next-intl';
import { useId } from 'react';
import type { AgentQuestion } from '@/lib/chat/types';
import { OptionCards } from './option-cards';

export function QuestionCard({
  question,
  onAnswer,
}: {
  question: AgentQuestion;
  onAnswer?: (text: string) => void;
}) {
  const t = useTranslations('chat.question');
  const titleId = useId();
  return (
    <div className="flex flex-col gap-2">
      <section
        aria-labelledby={titleId}
        className="border-line bg-ground flex flex-col gap-2.5 rounded-lg border p-4"
      >
        <span className="text-label text-muted uppercase">{t('label')}</span>
        <h2 id={titleId} className="text-ui-strong">
          {question.question}
        </h2>
        {onAnswer && (
          <>
            {question.options.length > 0 && (
              <OptionCards options={question.options} onPick={(o) => onAnswer(o.label)} />
            )}
            <div className="text-small text-muted flex items-center justify-between gap-3">
              <span>{question.options.length > 0 ? t('hint') : t('hintFree')}</span>
              <button
                type="button"
                onClick={() => onAnswer(t('skipMessage'))}
                className="text-ui-strong text-accent-ink hover:text-accent-ink/80 shrink-0 phone:min-h-11"
              >
                {t('skip')}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
