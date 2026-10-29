'use client';

import { ArrowUp } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { FormEvent, RefObject } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { DictationButton } from './dictation-button';

/** The reply box under a conversation, and «Ferma» above it while a run goes. */
export function ChatComposer({
  draft,
  onDraft,
  composer,
  placeholder,
  offline,
  isStreaming,
  stopping,
  narrow,
  onSubmit,
  onStop,
  autoFocus = false,
}: {
  draft: string;
  onDraft: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  placeholder: string;
  offline: boolean;
  isStreaming: boolean;
  stopping: boolean;
  /** Beside the list pane: the thread is 380 px wide. */
  narrow: boolean;
  onSubmit: (e?: FormEvent) => void;
  onStop: () => void;
  /** Focus the box when it mounts: a draft link just filled it. */
  autoFocus?: boolean;
}) {
  const t = useTranslations('chat');
  const column = cn('mx-auto w-full', narrow ? 'px-4' : 'max-w-chat-column px-4');
  return (
    <>
      {isStreaming && (
        <div className={cn(column, 'pb-2')}>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="phone:h-11"
            onClick={onStop}
            disabled={stopping}
          >
            <span aria-hidden="true" className="bg-ink size-2.5 rounded-xs" />
            {stopping ? t('stopping') : t('stop')}
          </Button>
        </div>
      )}
      <form onSubmit={onSubmit} className={cn(column, 'pb-5')}>
        <div
          className={cn(
            'flex min-h-composer items-end gap-2.5 rounded-lg border py-1.5 pr-1.5 pl-3.5 transition-[border,box-shadow] focus-within:ring-3 focus-within:ring-accent/50',
            offline ? 'border-line bg-raised' : 'border-line-strong bg-ground',
          )}
        >
          <Textarea
            ref={composer}
            autoFocus={autoFocus}
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) onSubmit(e);
            }}
            aria-label={t('composerLabel')}
            disabled={offline}
            placeholder={placeholder}
            rows={1}
            className="field-sizing-content max-h-48 min-h-0 resize-none border-0 bg-transparent px-0 py-1 shadow-none focus-visible:ring-0 md:text-ui"
          />
          <DictationButton
            text={draft}
            onText={onDraft}
            composer={composer}
            disabled={offline}
            size="icon-sm"
          />
          <Button
            type="submit"
            size="icon-sm"
            className="phone:size-11"
            disabled={offline || isStreaming || !draft.trim()}
            aria-label={t('send')}
          >
            <ArrowUp />
          </Button>
        </div>
      </form>
    </>
  );
}
