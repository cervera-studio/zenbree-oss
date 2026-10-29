import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import { blankStep, CHANNELS, hasCopyB, hasLinkedIn, type Step, toApiSteps } from './steps';

describe('toApiSteps', () => {
  it('renumbers, clears what the channel ignores and drops unknown keys', () => {
    const first = { ...blankStep(), subject: 'Hi {{firstName}}', body: 'Hello', extra: 'x' };
    const followUp = { ...blankStep(first), subject: 'left over', body: 'Again', step: 7 };
    const invite: Step = {
      step: 9,
      channel: 'linkedin_connect',
      subject: 'nope',
      body: '',
      sendAfterDays: 4.7,
      threaded: true,
    };
    expect(toApiSteps([first, followUp, invite])).toEqual([
      {
        step: 1,
        channel: 'email',
        subject: 'Hi {{firstName}}',
        body: 'Hello',
        sendAfterDays: 0,
        threaded: false,
      },
      { step: 2, channel: 'email', subject: '', body: 'Again', sendAfterDays: 3, threaded: true },
      {
        step: 3,
        channel: 'linkedin_connect',
        subject: '',
        body: '',
        sendAfterDays: 4,
        threaded: false,
      },
    ]);
  });
});

const copy = fc.record({ subject: fc.string(), body: fc.string() });
const anyStep: fc.Arbitrary<Step> = fc.record(
  {
    step: fc.integer({ min: -3, max: 20 }),
    channel: fc.constantFrom(...CHANNELS),
    subject: fc.string(),
    body: fc.string(),
    sendAfterDays: fc.double({ min: -5, max: 60, noNaN: true }),
    threaded: fc.boolean(),
    b: fc.option(copy, { nil: undefined }),
  },
  { requiredKeys: ['step', 'channel', 'subject', 'body', 'sendAfterDays'] },
);

describe('toApiSteps with copy B (#934)', () => {
  it('keeps every B copy on its step, and an empty one only on an invite', () => {
    fc.assert(
      fc.property(fc.array(anyStep, { maxLength: 12 }), (steps) => {
        const out = toApiSteps(steps);
        steps.forEach((s, i) => {
          if (!s.b) return;
          const subject = (s.channel === 'email' && !s.threaded) || s.channel === 'hubspot_task';
          const b = { subject: subject ? s.b.subject : '', body: s.b.body };
          // Elsewhere an empty B means «same as A» in the builder, and the API would send it empty.
          const kept = b.subject || b.body || s.channel === 'linkedin_connect';
          expect(out[i]?.b).toEqual(kept ? b : undefined);
        });
      }),
    );
  });

  it('drops an empty copy B from an email, which then tests nothing', () => {
    const empty = { subject: '', body: '' };
    const email = toApiSteps([{ ...blankStep(), body: 'A', b: empty }]);
    expect(email[0]?.b).toBe(undefined);
    expect(hasCopyB(email)).toBe(false);
  });

  it('keeps an invite with no note as copy B, a test the API runs', () => {
    const invite: Step = {
      ...blankStep(),
      channel: 'linkedin_connect',
      body: 'Ciao',
      threaded: false,
    };
    const steps = toApiSteps([{ ...invite, b: { subject: '', body: '' } }]);
    expect(steps).toEqual([{ ...invite, b: { subject: '', body: '' } }]);
    expect(hasCopyB(steps)).toBe(true);
  });

  it('is idempotent, B copy included', () => {
    fc.assert(
      fc.property(fc.array(anyStep, { maxLength: 12 }), (steps) => {
        const once = toApiSteps(steps);
        expect(toApiSteps(once)).toEqual(once);
      }),
    );
  });

  it('clears the B subject wherever it clears the A subject', () => {
    const steps = toApiSteps([
      { ...blankStep(), subject: 'A', body: 'a', b: { subject: 'B', body: 'b' } },
      { ...blankStep(blankStep()), body: 'again', b: { subject: 'left over', body: 'B again' } },
    ]);
    expect(steps.map((s) => s.b)).toEqual([
      { subject: 'B', body: 'b' },
      { subject: '', body: 'B again' },
    ]);
  });
});

describe('a HubSpot task step', () => {
  const task: Step = {
    step: 1,
    channel: 'hubspot_task',
    subject: 'Idea',
    body: 'Scrivile tu',
    sendAfterDays: 0,
    threaded: true,
  };

  it('keeps its subject, is never threaded and is not a LinkedIn step', () => {
    expect(toApiSteps([task])).toEqual([{ ...task, threaded: false }]);
    expect(hasLinkedIn([task])).toBe(false);
    expect(hasLinkedIn([task, { ...task, channel: 'linkedin_connect' }])).toBe(true);
  });
});
