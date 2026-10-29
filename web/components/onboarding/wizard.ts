import type {
  completeOnboardingAction,
  proposeSegmentsAction,
  saveProfileAction,
} from '@/app/onboarding/actions';
import type { BusinessProfile, OnboardingState, Segment } from '@/lib/onboarding/schema';
import type { StudyInput, StudyProgress, studyCompany } from '@/lib/onboarding/study';
import { siteFromEmail } from './email-site';

export const STEPS = ['company', 'profile', 'segments'] as const;
export type StepId = (typeof STEPS)[number];

// The API caps (api/app/onboarding/routes.py and study.py).
export const LIMITS = {
  website: 500,
  description: 4000,
  line: 280,
  paragraph: 1200,
  items: 8,
} as const;

export type ListKey = 'personas' | 'painPoints' | 'buyingTriggers' | 'markets' | 'exclusions';
export type TextKey = 'summary' | 'solution' | 'proof';

export type CompanyDraft = { website: string; description: string };

export type StudyCommand = {
  task: 'study';
  input: StudyInput;
  pages: number;
  drafting: boolean;
  offer: string | null;
};

/** What the wizard is waiting on. `pick` keeps the segment so its card can show the spinner. */
export type Command =
  | StudyCommand
  | { task: 'propose'; profile: BusinessProfile }
  | { task: 'pick'; segment: Segment }
  | { task: 'skip' };

type Shared = {
  company: CompanyDraft;
  segments: Segment[];
  busy: Command | null;
  /** The failure's error code, shown through `useErrorText`. */
  error: string | null;
};

export type WizardState = Shared &
  (
    | { step: 'company'; profile: BusinessProfile | null }
    | { step: 'profile' | 'segments'; profile: BusinessProfile }
  );

export type WizardEvent =
  | { type: 'editCompany'; patch: Partial<CompanyDraft> }
  | { type: 'editProfile'; profile: BusinessProfile }
  | { type: 'back' }
  | { type: 'start'; command: Command }
  | { type: 'progress'; progress: StudyProgress }
  | { type: 'cancel' }
  | { type: 'studied'; profile: BusinessProfile }
  | { type: 'proposed'; profile: BusinessProfile; segments: Segment[] }
  | { type: 'failed'; error: string };

/** Resume where the user left off: segments, then profile, then the company form. With nothing
 *  stored yet, the site is guessed from the user's work email. */
export function initialState(initial: OnboardingState, email?: string | null): WizardState {
  const stored = initial.website !== null || initial.description !== null;
  const shared: Shared = {
    // The field shows `https://` as a fixed prefix, and the API stores the URL with it.
    company: {
      website: stored ? (initial.website ?? '').replace(/^https:\/\//i, '') : siteFromEmail(email),
      description: initial.description ?? '',
    },
    segments: initial.segments,
    busy: null,
    error: null,
  };
  if (!initial.profile) return { ...shared, step: 'company', profile: null };
  const step = initial.segments.length ? 'segments' : 'profile';
  return { ...shared, step, profile: initial.profile };
}

export function redoState(initial: OnboardingState, email?: string | null): WizardState {
  return { ...initialState(initial, email), step: 'company' };
}

export function studyCommand(input: StudyInput): StudyCommand {
  return { task: 'study', input, pages: 0, drafting: false, offer: null };
}

function advance(study: StudyCommand, progress: StudyProgress): StudyCommand {
  switch (progress.type) {
    case 'page':
      return { ...study, pages: study.pages + 1 };
    case 'drafting':
      return { ...study, drafting: true };
    case 'offer':
      return { ...study, offer: progress.summary };
  }
}

export function reduce(state: WizardState, event: WizardEvent): WizardState {
  switch (event.type) {
    case 'editCompany':
      return { ...state, company: { ...state.company, ...event.patch } };
    case 'editProfile':
      return state.step === 'profile' ? { ...state, profile: event.profile } : state;
    case 'back':
      if (state.busy) return state;
      if (state.step === 'segments') return { ...state, step: 'profile', error: null };
      if (state.step === 'profile') return { ...state, step: 'company', error: null };
      return state;
    case 'start':
      return { ...state, busy: event.command, error: null };
    case 'progress':
      return state.busy?.task === 'study'
        ? { ...state, busy: advance(state.busy, event.progress) }
        : state;
    case 'cancel':
      return state.busy?.task === 'study' ? { ...state, busy: null, error: null } : state;
    case 'studied':
      return { ...state, step: 'profile', profile: event.profile, segments: [], busy: null };
    case 'proposed':
      return {
        ...state,
        step: 'segments',
        profile: event.profile,
        segments: event.segments,
        busy: null,
      };
    case 'failed':
      return { ...state, busy: null, error: event.error };
  }
}

/** The company form's submit boundary: null until there is a website or a description. */
export function parseCompany(draft: CompanyDraft): StudyInput | null {
  const website = draft.website.trim();
  const description = draft.description.trim();
  if (!website && !description) return null;
  return { website: website || undefined, description: description || undefined };
}

const LIST_KEYS: readonly ListKey[] = [
  'personas',
  'painPoints',
  'buyingTriggers',
  'markets',
  'exclusions',
];

/** Blank items are kept while editing (a new row starts empty) and dropped on save. */
function cleanProfile(profile: BusinessProfile): BusinessProfile {
  const out = { ...profile };
  for (const key of LIST_KEYS) out[key] = out[key].map((v) => v.trim()).filter(Boolean);
  return out;
}

export type PhaseStatus = 'pending' | 'running' | 'done';
export type StudyPhase =
  | { id: 'site'; status: PhaseStatus; pages: number }
  | { id: 'offer'; status: PhaseStatus; summary: string | null }
  | { id: 'profile'; status: PhaseStatus };

export function studyPhases(study: StudyCommand): StudyPhase[] {
  const offered = study.offer !== null;
  const offer: StudyPhase = {
    id: 'offer',
    status: offered ? 'done' : study.drafting ? 'running' : 'pending',
    summary: study.offer,
  };
  const profile: StudyPhase = { id: 'profile', status: offered ? 'running' : 'pending' };
  if (!study.input.website) return [offer, profile];
  const site: StudyPhase = {
    id: 'site',
    status: study.drafting ? 'done' : 'running',
    pages: study.pages,
  };
  return [site, offer, profile];
}

export type WizardDeps = {
  study: typeof studyCompany;
  saveProfile: typeof saveProfileAction;
  proposeSegments: typeof proposeSegmentsAction;
  complete: typeof completeOnboardingAction;
  dispatch: (event: WizardEvent) => void;
  navigate: (href: string) => void;
  signal: AbortSignal;
};

// A command sent while another runs is dropped: a study racing a skip would spend a free run.
export async function run(state: WizardState, command: Command, deps: WizardDeps): Promise<void> {
  if (state.busy) return;
  const live: WizardDeps = {
    ...deps,
    dispatch: (event) => {
      if (!deps.signal.aborted) deps.dispatch(event);
    },
  };
  live.dispatch({ type: 'start', command });
  try {
    const failure = await perform(command, live);
    if (failure) live.dispatch({ type: 'failed', error: failure.code });
  } catch {
    // The request itself failed (offline, deploy in progress), or the cancel aborted it.
    live.dispatch({ type: 'failed', error: 'INTERNAL.SERVICE_UNAVAILABLE' });
  }
}

type Failure = { code: string } | null;

async function perform(command: Command, deps: WizardDeps): Promise<Failure> {
  switch (command.task) {
    case 'study': {
      const studied = await deps.study(
        command.input,
        (progress) => deps.dispatch({ type: 'progress', progress }),
        deps.signal,
      );
      if (!studied.success) return studied.error;
      deps.dispatch({ type: 'studied', profile: studied.data.profile });
      return null;
    }
    case 'propose': {
      const saved = await deps.saveProfile(cleanProfile(command.profile));
      if (!saved.success) return saved.error;
      const proposed = await deps.proposeSegments();
      if (!proposed.success) return proposed.error;
      deps.dispatch({
        type: 'proposed',
        profile: saved.data.profile,
        segments: proposed.data.segments,
      });
      return null;
    }
    case 'pick':
    case 'skip': {
      const done = await deps.complete(command.task === 'pick' ? command.segment : null);
      if (!done.success) return done.error;
      // `busy` stays set: the buttons stay disabled until the app page replaces this one.
      deps.navigate(done.data.sessionId ? `/?s=${done.data.sessionId}` : '/');
      return null;
    }
  }
}
