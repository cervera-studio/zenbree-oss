import { useFormatter, useTranslations } from 'next-intl';
import { StatusDot, type Tone } from '@/components/ui/badge';
import { useErrorText } from '@/i18n/error-text';
import type { Automation, AutomationRun, RunStep } from '@/lib/automations/api';
import { ERROR_CODES, known } from '@/lib/automations/run-view';
import { campaignStatusKey, keyUnreadable } from '@/lib/integrations/instantly-campaign';

const RUN_TONE: Record<AutomationRun['status'], Tone> = {
  queued: 'neutral',
  running: 'accent',
  succeeded: 'positive',
  failed: 'negative',
  cancelled: 'neutral',
  unknown: 'warning',
};

const STEP_TONE: Record<RunStep['status'], Tone> = {
  pending: 'neutral',
  running: 'accent',
  succeeded: 'positive',
  skipped: 'neutral',
  failed: 'negative',
  uncertain: 'warning',
};

export function RunStatus({ status }: { status: AutomationRun['status'] }) {
  const t = useTranslations('automations.runStatus');
  return <StatusDot tone={RUN_TONE[status]}>{t(status)}</StatusDot>;
}

export function StepStatus({ status }: { status: RunStep['status'] }) {
  const t = useTranslations('automations.step.status');
  return <StatusDot tone={STEP_TONE[status]}>{t(status)}</StatusDot>;
}

export function StateLabel({ enabled }: { enabled: boolean }) {
  const t = useTranslations('automations.state');
  return (
    <StatusDot tone={enabled ? 'positive' : 'warning'}>
      {t(enabled ? 'active' : 'paused')}
    </StatusDot>
  );
}

export function useTriggerText() {
  const t = useTranslations('automations.trigger');
  return (a: Pick<Automation, 'triggerKind' | 'cadence'>) => {
    switch (a.triggerKind) {
      case 'schedule':
        return a.cadence ? t(a.cadence) : t('manual');
      case 'webhook':
        return t('webhook');
      case 'signal':
        return t('signal');
      case 'manual':
        return t('manual');
    }
  };
}

export function useWhen() {
  const format = useFormatter();
  return (iso: string | null) =>
    iso ? format.dateTime(new Date(iso), { dateStyle: 'medium', timeStyle: 'short' }) : '—';
}

export function useCodeText() {
  const t = useTranslations('automations.code');
  return (code: string) => (known(ERROR_CODES, code) ? t(code) : t('other'));
}

/** What the Instantly step says when it fails with `stepCode` after the vendor answered `vendorCode`: the step text,
 *  except for a key that cannot be decrypted, where only the vendor code says what to fix. The run page and the
 *  editor's preview both say it this way. */
export function useInstantlyStepText() {
  const codeText = useCodeText();
  const errorText = useErrorText();
  return (stepCode: string, vendorCode: unknown) =>
    keyUnreadable(vendorCode) ? errorText({ code: vendorCode }) : codeText(stepCode);
}

/** An Instantly campaign status number in words, as the lists' Instantly dialog names it. */
export function useInstantlyStatus() {
  const t = useTranslations('lists.instantly.campaignStatus');
  return (status: number) => t(campaignStatusKey(status));
}
