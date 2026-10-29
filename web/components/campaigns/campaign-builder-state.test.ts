import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import type { Preview } from '@/lib/campaigns/api';
import type { CampaignField } from '@/lib/campaigns/fields';
import { CHANNELS, type Step } from '@/lib/campaigns/steps';
import { type Action, initialState, reducer } from './campaign-builder-state';

const step = (n: number, body = `body ${n}`) =>
  ({ step: n, channel: 'email', subject: '', body, sendAfterDays: 0 }) as Step;
const items = (label: string) => [{ label }] as unknown as Preview['items'];
const city: CampaignField = { token: 'city', source: { kind: 'column', key: 'city' } };

const start = (steps: Step[] = [step(1), step(2)]) =>
  initialState({ steps, fields: [], items: items('server') });
const run = (actions: Action[], from = start()) => actions.reduce(reducer, from);

describe('campaign builder reducer', () => {
  it('opens the first step when there is one, with the server preview ready', () => {
    expect(start()).toEqual({
      steps: [step(1), step(2)],
      fields: [],
      dirty: false,
      abTest: false,
      open: 0,
      notice: null,
      preview: { items: items('server'), status: 'ready' },
    });
    expect(start([]).open).toBeNull();
  });

  it('an edit marks the draft dirty and starts the preview, keeping the open step', () => {
    expect(run([{ type: 'stepChanged', index: 0, step: step(1, 'changed') }])).toMatchObject({
      steps: [step(1, 'changed'), step(2)],
      dirty: true,
      open: 0,
      preview: { items: items('server'), status: 'loading' },
    });
  });

  it('adding a step opens it, removing one closes every step, undo puts it back in place', () => {
    const added = run([{ type: 'stepAdded', channel: 'linkedin_connect' }]);
    expect(added).toMatchObject({ open: 2, dirty: true, preview: { status: 'loading' } });
    expect(added.steps[2]).toMatchObject({ step: 3, channel: 'linkedin_connect', body: '' });
    const removed = reducer(added, { type: 'stepRemoved', index: 1 });
    expect(removed).toMatchObject({ steps: [step(1), added.steps[2]], open: null, dirty: true });
    const restored = reducer(removed, { type: 'stepRestored', index: 1, step: step(2) });
    expect(restored.steps).toEqual(added.steps);
  });

  it('undo keeps the same step open, though the step it puts back moves it down', () => {
    const removed = run([
      { type: 'stepRemoved', index: 0 },
      { type: 'stepToggled', index: 0 },
    ]);
    const restored = reducer(removed, { type: 'stepRestored', index: 0, step: step(1) });
    expect(restored.steps[restored.open ?? -1]).toEqual(step(2));
    expect(reducer(removed, { type: 'stepRestored', index: 1, step: step(1) }).open).toBe(0);
  });

  it('a field edit marks the draft dirty and loads the preview, keeping the steps and the open step', () => {
    expect(run([{ type: 'fieldsEdited', fields: [city] }])).toMatchObject({
      steps: [step(1), step(2)],
      fields: [city],
      dirty: true,
      open: 0,
      preview: { status: 'loading' },
    });
  });

  it('toggling the open step closes it, toggling another opens that one', () => {
    expect(run([{ type: 'stepToggled', index: 0 }]).open).toBeNull();
    expect(run([{ type: 'stepToggled', index: 1 }]).open).toBe(1);
  });

  it('a failed preview keeps the last items, and the next edit loads again', () => {
    const failed = run([
      { type: 'stepChanged', index: 0, step: step(1) },
      { type: 'previewFailed' },
    ]);
    expect(failed.preview).toEqual({ items: items('server'), status: 'failed' });
    const retried = reducer(failed, { type: 'stepChanged', index: 0, step: step(1, 'again') });
    expect(retried.preview).toEqual({ items: items('server'), status: 'loading' });
  });

  it('a preview answer replaces the items in any status: the ticket, not the status, drops stale ones', () => {
    const loaded = run([
      { type: 'stepChanged', index: 0, step: step(1) },
      { type: 'previewLoaded', items: items('first') },
    ]);
    expect(loaded.preview).toEqual({ items: items('first'), status: 'ready' });
    const newer = reducer(loaded, { type: 'previewLoaded', items: items('newer') });
    expect(newer.preview).toEqual({ items: items('newer'), status: 'ready' });
  });

  it('saving before activation cleans the draft and keeps the edited steps', () => {
    const saved = run([
      { type: 'stepChanged', index: 0, step: step(1, 'edited') },
      { type: 'stepsSaved' },
    ]);
    expect(saved).toMatchObject({ steps: [step(1, 'edited'), step(2)], dirty: false });
  });

  it('the saved campaign replaces the steps and the fields, with its notice when it has one', () => {
    const server = [step(1, 'from server')];
    const edited = run([
      { type: 'stepChanged', index: 0, step: step(1) },
      { type: 'fieldsEdited', fields: [] },
      { type: 'requestStarted' },
    ]);
    expect(
      reducer(edited, { type: 'campaignLoaded', steps: server, fields: [city], notice: 'Saved.' }),
    ).toMatchObject({ steps: server, fields: [city], dirty: false, notice: 'Saved.' });
    expect(reducer(edited, { type: 'campaignLoaded', steps: server, fields: [] })).toMatchObject({
      steps: server,
      dirty: false,
      notice: null,
    });
  });
});

// Each generated step carries its identity in its body, and its copy B in `<body>+B`: the property checks that a
// copy B is still on the step it was written for, whatever the edits did to the list.
const tested = (n: number, withB: boolean): Step => ({
  ...step(n, `s${n}`),
  ...(withB ? { b: { subject: `B${n}`, body: `s${n}+B` } } : {}),
});
const edit = fc.oneof(
  fc.record({ type: fc.constant('add' as const), channel: fc.constantFrom(...CHANNELS) }),
  fc.record({ type: fc.constant('remove' as const), at: fc.nat() }),
  fc.record({ type: fc.constant('undo' as const) }),
  fc.record({ type: fc.constant('subject' as const), at: fc.nat(), subject: fc.string() }),
);
type Edit = typeof edit extends fc.Arbitrary<infer E> ? E : never;

type Removed = { index: number; step: Step };

/** The action the builder dispatches for an edit: «Annulla» restores the last removed step where it was. */
function action(state: ReturnType<typeof start>, e: Edit, undo: Removed[]): Action | null {
  const n = state.steps.length;
  switch (e.type) {
    case 'add':
      return { type: 'stepAdded', channel: e.channel };
    case 'remove': {
      if (!n) return null;
      const index = e.at % n;
      undo.push({ index, step: state.steps[index] as Step });
      return { type: 'stepRemoved', index };
    }
    case 'undo': {
      const last = undo.pop();
      return last ? { type: 'stepRestored', ...last } : null;
    }
    case 'subject': {
      if (!n) return null;
      const index = e.at % n;
      return {
        type: 'stepChanged',
        index,
        step: { ...(state.steps[index] as Step), subject: e.subject },
      };
    }
  }
}

function play(from: ReturnType<typeof start>, edits: Edit[]) {
  const undo: Removed[] = [];
  return edits.reduce((state, e) => {
    const next = action(state, e, undo);
    return next ? reducer(state, next) : state;
  }, from);
}

const startingSteps = fc
  .array(fc.boolean(), { maxLength: 8 })
  .map((withB) => withB.map((b, i) => tested(i + 1, b)));

describe('copy B in the builder (#934)', () => {
  it('any add, remove, undo and edit keeps each copy B on the step it was written for', () => {
    fc.assert(
      fc.property(startingSteps, fc.array(edit, { maxLength: 30 }), (steps, edits) => {
        const written = new Set(steps.filter((s) => s.b).map((s) => s.body));
        for (const s of play(start(steps), edits).steps) {
          expect(s.b?.body).toBe(written.has(s.body) ? `${s.body}+B` : undefined);
        }
      }),
    );
  });

  it('turning the test off removes every copy B, and later edits bring none back', () => {
    fc.assert(
      fc.property(startingSteps, fc.array(edit, { maxLength: 30 }), (steps, edits) => {
        const off = reducer(start(steps), { type: 'testSwitched', on: false });
        expect(off.abTest).toBe(false);
        expect(play(off, edits).steps.every((s) => s.b === undefined)).toBe(true);
      }),
    );
  });

  it('undo after the test went off brings the step back without its copy B', () => {
    const removed = tested(1, true);
    const off = run(
      [
        { type: 'stepRemoved', index: 0 },
        { type: 'testSwitched', on: false },
        { type: 'stepRestored', index: 0, step: removed },
      ],
      start([removed, tested(2, false)]),
    );
    expect(off.steps.map((s) => s.b)).toEqual([undefined, undefined]);
    expect(off.steps[0]?.body).toBe('s1');
  });

  it('the switch starts on when a step has copy B, and turning it on changes no step', () => {
    expect(start([tested(1, true)]).abTest).toBe(true);
    expect(start([tested(1, false)]).abTest).toBe(false);
    const on = reducer(start([tested(1, false)]), { type: 'testSwitched', on: true });
    expect(on).toMatchObject({ abTest: true, dirty: false, steps: [tested(1, false)] });
  });

  it('a saved campaign keeps the switch on, also before any copy B is written', () => {
    const on = reducer(start(), { type: 'testSwitched', on: true });
    const saved = reducer(on, { type: 'campaignLoaded', steps: [step(1)], fields: [] });
    expect(saved.abTest).toBe(true);
  });
});
