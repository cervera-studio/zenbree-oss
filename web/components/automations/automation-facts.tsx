import { useTranslations } from 'next-intl';
import type { Automation } from '@/lib/automations/api';
import { StateLabel, useTriggerText } from './labels';

export function AutomationFacts({ automation: a }: { automation: Automation }) {
  const t = useTranslations('automations');
  const trigger = useTriggerText();
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <StateLabel enabled={a.enabled} />
      <span>{trigger(a)}</span>
      <span className="font-mono">{t('version', { version: a.version })}</span>
      <span>{t('head.cap', { cap: a.creditCap })}</span>
    </span>
  );
}
