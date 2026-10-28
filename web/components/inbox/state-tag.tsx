import { getTranslations } from 'next-intl/server';
import { Tag, type Tone } from '@/components/ui/badge';
import type { Label } from '@/lib/inbox/api';
import type { Message } from './types';

export const TONE: Record<Label | 'none' | Message['kind'], Tone> = {
  reply: 'neutral',
  interested: 'positive',
  meeting_request: 'accent',
  not_now: 'warning',
  not_interested: 'neutral',
  other: 'neutral',
  none: 'neutral',
  unsubscribe: 'negative',
  auto_reply: 'neutral',
};

/** A reply's label, or its state when it is an auto-reply or an opt-out (those carry no label). */
export async function StateTag({ message: m }: { message: Message }) {
  const t = await getTranslations('inbox');
  if (m.kind !== 'reply') return <Tag tone={TONE[m.kind]}>{t(`kind.${m.kind}`)}</Tag>;
  if (!m.label) return null;
  return <Tag tone={TONE[m.label]}>{t(`label.${m.label}`)}</Tag>;
}
