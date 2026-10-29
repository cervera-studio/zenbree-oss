import { Check } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { SignOutButton } from '@/components/app/sign-out-button';
import { Logo } from '@/components/logo';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { STEPS, type StepId } from './wizard';

export function OnboardingHeader({
  step,
  disabled,
  onSkip,
}: {
  step: StepId;
  disabled: boolean;
  onSkip: () => void;
}) {
  const t = useTranslations('onboarding');
  const current = STEPS.indexOf(step);
  return (
    // Phones put the stepper on its own row under the brand and the actions.
    <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-6 border-b border-line bg-ground px-4 desktop:h-topbar desktop:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] desktop:px-6">
      <div className="flex h-topbar items-center desktop:h-auto">
        <Logo />
      </div>

      <ol
        aria-label={t('step', { current: current + 1, total: STEPS.length })}
        className="col-span-2 row-start-2 flex items-center justify-center gap-3 pb-3 desktop:col-span-1 desktop:col-start-2 desktop:row-start-1 desktop:pb-0"
      >
        {STEPS.map((id, i) => {
          const on = i === current;
          const done = i < current;
          return (
            <li key={id} className="flex items-center gap-3">
              <span
                aria-current={on ? 'step' : undefined}
                className={cn(
                  'inline-flex h-control items-center gap-2 rounded-full pr-2.5 pl-1.5 whitespace-nowrap',
                  on
                    ? 'bg-accent-tint text-ui-strong text-accent-ink'
                    : 'text-ui text-soft phone:pr-1.5',
                )}
              >
                <span
                  className={cn(
                    'flex size-5 items-center justify-center rounded-full font-mono text-data-sm tabular-nums',
                    on || done ? 'bg-accent text-on-accent' : 'bg-raised text-soft',
                  )}
                >
                  {done ? <Check className="size-3" aria-hidden="true" /> : i + 1}
                </span>
                <span className={cn(!on && 'phone:sr-only')}>{t(`steps.${id}`)}</span>
              </span>
              {i < STEPS.length - 1 && (
                <span aria-hidden="true" className="h-px w-4 bg-line desktop:w-8" />
              )}
            </li>
          );
        })}
      </ol>

      <div className="flex items-center justify-end gap-1">
        <Button type="button" variant="ghost" disabled={disabled} onClick={onSkip}>
          {t('skip')}
        </Button>
        <SignOutButton />
      </div>
    </header>
  );
}
