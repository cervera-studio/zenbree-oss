import { describe, expect, it, vi } from 'vite-plus/test';
import type { BusinessProfile, OnboardingState, Segment } from '@/lib/onboarding/schema';
import type { StudyProgress } from '@/lib/onboarding/study';
import { AppError, fail, succeed } from '@/lib/types';
import {
  type Command,
  initialState,
  parseCompany,
  redoState,
  reduce,
  run,
  studyCommand,
  studyPhases,
  type WizardDeps,
  type WizardState,
} from './wizard';

const profile: BusinessProfile = {
  companyName: 'Acme',
  summary: 'Acme sells anvils.',
  personas: ['Head of ops'],
  markets: ['Italy'],
  painPoints: ['Slow deliveries'],
  solution: 'Faster anvils.',
  buyingTriggers: [],
  exclusions: [],
  proof: '',
};

function segment(name: string): Segment {
  return {
    name,
    who: `${name} buyers`,
    problem: 'p',
    outcome: 'o',
    criteria: {
      titles: ['CEO'],
      seniorities: [],
      industries: [],
      employeeRanges: [],
      locations: ['Italy'],
      keywords: [],
    },
  };
}

const fresh: OnboardingState = {
  needsOnboarding: true,
  website: null,
  description: null,
  profile: null,
  segments: [],
};

/** The real reducer behind the same `dispatch` the component passes to `run`. */
function harness(initial: OnboardingState, overrides: Partial<WizardDeps> = {}) {
  let state = initialState(initial);
  const controller = new AbortController();
  const deps: WizardDeps = {
    study: vi.fn<WizardDeps['study']>(async () => succeed({ profile })),
    saveProfile: vi.fn<WizardDeps['saveProfile']>(async (p) => succeed({ profile: p })),
    proposeSegments: vi.fn<WizardDeps['proposeSegments']>(async () =>
      succeed({ segments: [segment('Agencies'), segment('SaaS')] }),
    ),
    complete: vi.fn<WizardDeps['complete']>(async () => succeed({ sessionId: 'sess-1' })),
    dispatch: (event) => {
      state = reduce(state, event);
    },
    navigate: vi.fn(),
    signal: controller.signal,
    ...overrides,
  };
  return {
    deps,
    get state(): WizardState {
      return state;
    },
    send: (command: Command) => run(state, command, deps),
    dispatch: deps.dispatch,
    /** What the wizard does on «Cambia sito». */
    cancel: () => {
      controller.abort();
      deps.dispatch({ type: 'cancel' });
    },
  };
}

describe('initialState', () => {
  it('resumes at the furthest step the saved state supports', () => {
    expect(initialState(fresh).step).toBe('company');
    expect(initialState({ ...fresh, profile }).step).toBe('profile');
    expect(initialState({ ...fresh, profile, segments: [segment('A')] }).step).toBe('segments');
  });

  it('restarts at the company form on a redo, keeping the profile and the site', () => {
    const done: OnboardingState = {
      needsOnboarding: false,
      website: 'https://acme.com',
      description: null,
      profile,
      segments: [segment('A')],
    };
    const redo = redoState(done);
    expect(redo.step).toBe('company');
    expect(redo.profile).toEqual(profile);
    expect(redo.company.website).toBe('acme.com');
    expect(initialState(done).step).toBe('segments');
  });

  it('shows a saved https site without the scheme the field already prints', () => {
    const state = initialState({ ...fresh, website: 'https://acme.com', description: 'Anvils' });
    expect(state.company).toEqual({ website: 'acme.com', description: 'Anvils' });
    expect(initialState({ ...fresh, website: 'http://acme.com' }).company.website).toBe(
      'http://acme.com',
    );
  });
});

describe('parseCompany', () => {
  it('blocks the company step until a website or a description is filled', () => {
    expect(parseCompany({ website: '', description: '' })).toBeNull();
    expect(parseCompany({ website: '   ', description: '\n ' })).toBeNull();
  });

  it('sends only the trimmed fields that were filled', () => {
    expect(parseCompany({ website: ' acme.com ', description: '' })).toEqual({
      website: 'acme.com',
      description: undefined,
    });
    expect(parseCompany({ website: '', description: ' We sell anvils ' })).toEqual({
      website: undefined,
      description: 'We sell anvils',
    });
  });
});

describe('run', () => {
  it('walks company → profile → segments and starts the first list with the picked segment', async () => {
    const w = harness(fresh);
    w.dispatch({ type: 'editCompany', patch: { website: 'acme.com' } });
    const input = parseCompany(w.state.company);
    if (!input) throw new Error('company should be valid');

    await w.send(studyCommand(input));
    expect(w.deps.study).toHaveBeenCalledWith(
      { website: 'acme.com', description: undefined },
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(w.state).toMatchObject({ step: 'profile', profile, busy: null, error: null });

    // A row added and left blank, plus padding: the action gets neither.
    const edited = { ...profile, personas: ['  Head of ops ', '', 'CFO'], markets: ['Italy', ' '] };
    w.dispatch({ type: 'editProfile', profile: edited });
    await w.send({ task: 'propose', profile: edited });
    expect(w.deps.saveProfile).toHaveBeenCalledWith({
      ...profile,
      personas: ['Head of ops', 'CFO'],
      markets: ['Italy'],
    });
    expect(w.deps.proposeSegments).toHaveBeenCalledTimes(1);
    expect(w.state.step).toBe('segments');
    expect(w.state.segments.map((s) => s.name)).toEqual(['Agencies', 'SaaS']);

    const picked = w.state.segments.find((s) => s.name === 'SaaS');
    if (!picked) throw new Error('SaaS should be proposed');
    await w.send({ task: 'pick', segment: picked });
    expect(w.deps.complete).toHaveBeenCalledWith(segment('SaaS'));
    expect(w.deps.navigate).toHaveBeenCalledWith('/?s=sess-1');
    // Still busy while the app page loads, so nothing can be clicked twice.
    expect(w.state.busy).toEqual({ task: 'pick', segment: picked });
  });

  it('skips with no segment and lands on the empty home', async () => {
    const w = harness(fresh, {
      complete: vi.fn<WizardDeps['complete']>(async () => succeed({ sessionId: null })),
    });
    await w.send({ task: 'skip' });
    expect(w.deps.complete).toHaveBeenCalledWith(null);
    expect(w.deps.navigate).toHaveBeenCalledWith('/');
  });

  it('stays on the company step with the unreadable-site message', async () => {
    const w = harness(fresh, {
      study: vi.fn<WizardDeps['study']>(async () =>
        fail(new AppError('ONBOARDING.SITE_UNREADABLE', 'no', 422)),
      ),
    });
    await w.send(studyCommand({ website: 'nope.invalid' }));
    expect(w.state).toMatchObject({
      step: 'company',
      busy: null,
      error: 'ONBOARDING.SITE_UNREADABLE',
    });
  });

  it('keeps the edited profile on screen when segments fail after the save', async () => {
    const w = harness(
      { ...fresh, profile },
      {
        proposeSegments: vi.fn<WizardDeps['proposeSegments']>(async () =>
          fail(new AppError('ONBOARDING.LIMIT', 'cap', 429)),
        ),
      },
    );
    const edited = { ...profile, companyName: 'Acme Spa', personas: ['Head of ops', ''] };
    w.dispatch({ type: 'editProfile', profile: edited });
    await w.send({ task: 'propose', profile: edited });
    expect(w.deps.saveProfile).toHaveBeenCalledWith({ ...edited, personas: ['Head of ops'] });
    expect(w.state).toMatchObject({
      step: 'profile',
      profile: edited,
      busy: null,
      error: 'ONBOARDING.LIMIT',
    });
  });

  it('drops a command sent while another is running', async () => {
    let finish = (_: Awaited<ReturnType<WizardDeps['complete']>>) => {};
    const w = harness(fresh, {
      complete: vi.fn<WizardDeps['complete']>(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
    });
    const skipping = w.send({ task: 'skip' });
    await w.send(studyCommand({ website: 'acme.com' }));
    expect(w.deps.study).not.toHaveBeenCalled();
    expect(w.state.busy).toEqual({ task: 'skip' });
    finish(succeed({ sessionId: null }));
    await skipping;
    expect(w.deps.navigate).toHaveBeenCalledWith('/');
  });

  it('stays on the segments with the buttons free when the pick fails', async () => {
    const w = harness(
      { ...fresh, profile, segments: [segment('Agencies')] },
      {
        complete: vi.fn<WizardDeps['complete']>(async () =>
          fail(new AppError('INTERNAL', 'boom', 500)),
        ),
      },
    );
    await w.send({ task: 'pick', segment: segment('Agencies') });
    expect(w.deps.navigate).not.toHaveBeenCalled();
    expect(w.state).toMatchObject({ step: 'segments', busy: null, error: 'INTERNAL' });
  });

  it('turns a rejected server action into the generic error instead of hanging', async () => {
    const w = harness(fresh, {
      study: vi.fn<WizardDeps['study']>(async () => {
        throw new Error('network');
      }),
    });
    await w.send(studyCommand({ website: 'acme.com' }));
    expect(w.state).toMatchObject({ busy: null, error: 'INTERNAL.SERVICE_UNAVAILABLE' });
  });
});

describe('back', () => {
  it('returns to earlier steps with everything the user entered', async () => {
    const w = harness(fresh);
    w.dispatch({ type: 'editCompany', patch: { website: 'acme.com', description: 'Anvils' } });
    await w.send(studyCommand({ website: 'acme.com', description: 'Anvils' }));
    const edited = { ...profile, companyName: 'Acme Spa' };
    w.dispatch({ type: 'editProfile', profile: edited });
    await w.send({ task: 'propose', profile: edited });

    w.dispatch({ type: 'back' });
    expect(w.state).toMatchObject({ step: 'profile', profile: { companyName: 'Acme Spa' } });
    w.dispatch({ type: 'back' });
    expect(w.state.step).toBe('company');
    expect(w.state.company).toEqual({ website: 'acme.com', description: 'Anvils' });
  });

  it('does nothing while a request is running', () => {
    const w = harness({ ...fresh, profile });
    w.dispatch({ type: 'start', command: { task: 'propose', profile } });
    w.dispatch({ type: 'back' });
    expect(w.state.step).toBe('profile');
  });
});

describe('site prefill', () => {
  it('fills the site from a work email when nothing is stored', () => {
    expect(initialState(fresh, 'ada@acme.it').company.website).toBe('acme.it');
    expect(initialState(fresh, 'ada@gmail.com').company.website).toBe('');
    expect(initialState(fresh).company.website).toBe('');
  });

  it('never replaces what the project stored, a description alone included', () => {
    const site = { ...fresh, website: 'https://beta.com' };
    expect(initialState(site, 'ada@acme.it').company.website).toBe('beta.com');
    const described = { ...fresh, description: 'Anvils', profile };
    expect(redoState(described, 'ada@acme.it').company).toEqual({
      website: '',
      description: 'Anvils',
    });
  });
});

describe('study progress', () => {
  const step = (state: WizardState, progress: StudyProgress) =>
    reduce(state, { type: 'progress', progress });
  const phases = (state: WizardState) =>
    state.busy?.task === 'study' ? studyPhases(state.busy) : [];

  it('ticks the phases off as the stream reports them', () => {
    let state = reduce(initialState(fresh), {
      type: 'start',
      command: studyCommand({ website: 'acme.it' }),
    });
    state = step(step(state, { type: 'page', url: 'a' }), { type: 'page', url: 'b' });
    expect(phases(state)).toEqual([
      { id: 'site', status: 'running', pages: 2 },
      { id: 'offer', status: 'pending', summary: null },
      { id: 'profile', status: 'pending' },
    ]);
    state = step(state, { type: 'drafting' });
    expect(phases(state).map((p) => p.status)).toEqual(['done', 'running', 'pending']);
    state = step(state, { type: 'offer', summary: 'Anvils for agencies' });
    expect(phases(state)).toEqual([
      { id: 'site', status: 'done', pages: 2 },
      { id: 'offer', status: 'done', summary: 'Anvils for agencies' },
      { id: 'profile', status: 'running' },
    ]);
  });

  it('has no site row for a description-only study', () => {
    const state = reduce(initialState(fresh), {
      type: 'start',
      command: studyCommand({ description: 'Anvils' }),
    });
    expect(phases(step(state, { type: 'drafting' }))).toEqual([
      { id: 'offer', status: 'running', summary: null },
      { id: 'profile', status: 'pending' },
    ]);
  });

  it('shows the progress the study reports while it runs', async () => {
    let seen: WizardState | null = null;
    const w = harness(fresh, {
      study: vi.fn<WizardDeps['study']>(async (_input, onProgress) => {
        onProgress({ type: 'page', url: 'https://acme.it' });
        onProgress({ type: 'drafting' });
        seen = w.state;
        return succeed({ profile });
      }),
    });
    await w.send(studyCommand({ website: 'acme.it' }));
    expect(seen).toMatchObject({ busy: { task: 'study', pages: 1, drafting: true, offer: null } });
    expect(w.state).toMatchObject({ step: 'profile', busy: null });
  });
});

describe('cancel', () => {
  it('stops the study and returns to the form with the inputs kept and no error', async () => {
    let signal: AbortSignal | null = null;
    const w = harness(fresh, {
      study: vi.fn<WizardDeps['study']>(
        (_input, onProgress, s) =>
          new Promise((_, reject) => {
            signal = s;
            onProgress({ type: 'page', url: 'https://acme.it' });
            s.addEventListener('abort', () => {
              onProgress({ type: 'drafting' }); // a frame that was already on its way
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    });
    w.dispatch({ type: 'editCompany', patch: { website: 'acme.it', description: 'Anvils' } });
    const studying = w.send(studyCommand({ website: 'acme.it', description: 'Anvils' }));
    expect(w.state.busy).toMatchObject({ task: 'study', pages: 1 });
    w.cancel();
    await studying;
    expect(signal).toHaveProperty('aborted', true);
    expect(w.state).toMatchObject({ step: 'company', busy: null, error: null });
    expect(w.state.company).toEqual({ website: 'acme.it', description: 'Anvils' });
  });

  it('leaves any other running command alone', () => {
    const w = harness({ ...fresh, profile });
    w.dispatch({ type: 'start', command: { task: 'propose', profile } });
    w.dispatch({ type: 'cancel' });
    expect(w.state.busy).toEqual({ task: 'propose', profile });
  });
});
