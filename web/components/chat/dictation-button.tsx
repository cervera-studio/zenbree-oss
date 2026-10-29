'use client';

import { Mic, MicOff, Square } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { RefObject } from 'react';
import { Button } from '@/components/ui/button';
import { useDictation } from './use-dictation';

/** The mic beside a composer's send button: speech goes into the box, the user still sends it. */
export function DictationButton({
  text,
  onText,
  composer,
  disabled,
  size,
}: {
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  /** The send button's size, so the two match. */
  size: 'icon-xs' | 'icon-sm';
}) {
  const t = useTranslations('chat.dictation');
  const { state, toggle } = useDictation({ text, onText, composer, disabled });
  if (state === 'unsupported') return null;

  const listening = state === 'listening';
  const label = listening ? t('stop') : state === 'denied' ? t('denied') : t('start');
  return (
    <Button
      type="button"
      variant={listening ? 'soft' : 'ghost'}
      size={size}
      className="phone:size-11"
      onClick={toggle}
      disabled={disabled}
      aria-pressed={listening}
      aria-label={label}
      title={label}
    >
      {listening ? (
        <Square className="fill-current motion-safe:animate-pulse" />
      ) : state === 'denied' ? (
        <MicOff />
      ) : (
        <Mic />
      )}
    </Button>
  );
}
