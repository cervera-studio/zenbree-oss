'use client';

import { useRouter } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useReducer, useRef } from 'react';
import {
  completeOnboardingAction,
  proposeSegmentsAction,
  saveProfileAction,
} from '@/app/onboarding/actions';
import { useErrorText } from '@/i18n/error-text';
import type { OnboardingState } from '@/lib/onboarding/schema';
import { studyCompany } from '@/lib/onboarding/study';
import { OnboardingHeader } from './onboarding-header';
import { CompanyStep } from './step-company';
import { ProfileStep } from './step-profile';
import { SegmentsStep } from './step-segments';
import { type Command, initialState, redoState, reduce, run, studyCommand } from './wizard';

export function OnboardingWizard({
  initial,
  email,
  redo = false,
}: {
  initial: OnboardingState;
  email: string;
  redo?: boolean;
}) {
  const t = useTranslations('onboarding');
  const locale = useLocale();
  const errorText = useErrorText();
  const router = useRouter();
  const [state, dispatch] = useReducer(reduce, initial, (saved) =>
    (redo ? redoState : initialState)(saved, email),
  );
  const running = useRef<AbortController | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const shownStep = useRef(state.step);

  useEffect(() => {
    if (shownStep.current === state.step) return;
    shownStep.current = state.step;
    // The button that moved the user on is gone with its step: start the new one at its title.
    window.scrollTo(0, 0);
    root.current?.querySelector('h1')?.focus({ preventScroll: true });
  }, [state.step]);

  const send = (command: Command) => {
    // A command sent while one runs is dropped by run(): keep the running one's controller.
    if (state.busy) return;
    running.current = new AbortController();
    void run(state, command, {
      study: (input, onProgress, signal) => studyCompany({ ...input, locale }, onProgress, signal),
      saveProfile: saveProfileAction,
      proposeSegments: proposeSegmentsAction,
      complete: completeOnboardingAction,
      dispatch,
      navigate: (href) => router.push(href),
      signal: running.current.signal,
    });
  };
  const cancelStudy = () => {
    running.current?.abort();
    dispatch({ type: 'cancel' });
  };
  const backToMemory = () => router.push('/settings/memory');
  const skip = redo ? backToMemory : () => send({ task: 'skip' });
  const back = () => dispatch({ type: 'back' });
  const error = state.error && errorText({ code: state.error }, t('errors.generic'));

  return (
    <div ref={root} className="flex min-h-dvh flex-col">
      <OnboardingHeader step={state.step} disabled={state.busy !== null} onSkip={skip} />
      {state.step === 'company' && (
        <CompanyStep
          company={state.company}
          busy={state.busy}
          error={error}
          onChange={(patch) => dispatch({ type: 'editCompany', patch })}
          onSubmit={(input) => send(studyCommand(input))}
          onCancel={cancelStudy}
        />
      )}
      {state.step === 'profile' && (
        <ProfileStep
          profile={state.profile}
          busy={state.busy}
          error={error}
          onChange={(profile) => dispatch({ type: 'editProfile', profile })}
          onBack={back}
          onConfirm={() => send({ task: 'propose', profile: state.profile })}
        />
      )}
      {state.step === 'segments' && (
        <SegmentsStep
          segments={state.segments}
          busy={state.busy}
          error={error}
          onBack={back}
          onRegenerate={() => send({ task: 'propose', profile: state.profile })}
          onPick={(segment) => send({ task: 'pick', segment })}
          onSkip={skip}
        />
      )}
    </div>
  );
}
