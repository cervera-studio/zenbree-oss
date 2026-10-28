'use client';

import { ArrowUp } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { FormEvent, ReactNode, RefObject } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { DictationButton } from './dictation-button';

export function HomeComposer({
  text,
  onText,
  composer,
  onSubmit,
  disabled,
  placeholder,
  tools,
}: {
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onSubmit: (brief: string) => void;
  disabled: boolean;
  placeholder: string;
  tools?: ReactNode;
}) {
  const t = useTranslations('chat');
  const th = useTranslations('chat.home');

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const brief = text.trim();
    if (!brief || disabled) return;
    onSubmit(brief);
  };

  return (
    <form
      onSubmit={submit}
      className="border-line-strong bg-ground flex flex-col gap-3.5 rounded-lg border p-3.5 transition-shadow focus-within:ring-3 focus-within:ring-accent/50"
    >
      <Textarea
        ref={composer}
        // A draft link fills the box: the loaded screen mounts a new composer over the skeleton's.
        autoFocus={text !== ''}
        value={text}
        onChange={(e) => onText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
        }}
        aria-label={th('composerLabel')}
        disabled={disabled}
        placeholder={placeholder}
        rows={2}
        className="max-h-48 min-h-11.5 resize-none border-0 bg-transparent p-0 shadow-none focus-visible:ring-0 md:text-ui"
      />
      <div className="flex items-center gap-2">
        {tools}
        <span className="grow" />
        <span className="text-small text-muted phone:hidden">{th('planFree')}</span>
        <span className="text-small text-muted desktop:hidden">{th('planFreeShort')}</span>
        <DictationButton
          text={text}
          onText={onText}
          composer={composer}
          disabled={disabled}
          size="icon-xs"
        />
        <Button
          type="submit"
          size="icon-xs"
          className="phone:size-11"
          disabled={disabled || !text.trim()}
          aria-label={t('send')}
        >
          <ArrowUp />
        </Button>
      </div>
    </form>
  );
}
