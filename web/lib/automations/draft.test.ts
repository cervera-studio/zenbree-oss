import { describe, expect, it } from 'vite-plus/test';
import type { Definition } from './definition';
import {
  blankDraft,
  check,
  type Draft,
  fromAutomation,
  makesNewVersion,
  saveEffects,
  triggerBody,
} from './draft';

const PEOPLE = '00000000-0000-4000-8000-000000000011';
const ACCOUNTS = '00000000-0000-4000-8000-000000000012';
const CAMPAIGN = '00000000-0000-4000-8000-000000000013';
const WATCHLIST = '00000000-0000-4000-8000-000000000014';
const savedSignal = {
  watchlistId: WATCHLIST,
  watchlistName: 'Assunzioni',
  minScore: 80,
  since: '2026-10-21T08:00:00Z',
};
const onWatchlist = { kind: 'signal', watchlistId: WATCHLIST, minScore: 80 } as const;

// As the API stores it: every default filled, the seniorities in the order they were sent.
const SAVED: Definition = {
  listId: PEOPLE,
  steps: [
    {
      type: 'find_people',
      accountsListId: ACCOUNTS,
      titles: ['Head of Sales', 'CEO'],
      seniorities: ['vp', 'junior', 'c_suite'],
      perAccount: 3,
    },
    { type: 'qualify', brief: 'B2B SaaS in Italy', minScore: 50 },
    { type: 'add_to_campaign', campaignId: CAMPAIGN },
  ],
};
const saved = {
  name: 'Lead Milano',
  triggerKind: 'manual',
  cadence: null,
  creditCap: 20,
  signal: null,
} as const;

function switchOn(draft: Draft, kind: keyof Draft['steps']): Draft {
  return { ...draft, steps: { ...draft.steps, [kind]: { ...draft.steps[kind], on: true } } };
}

describe('the draft of an automation', () => {
  it('sends the steps that are on, in the run order, whatever order they were switched on in', () => {
    let draft: Draft = { ...blankDraft(), name: 'Lead Milano', listId: PEOPLE, creditCap: 30 };
    for (const kind of ['write_hubspot', 'find_emails', 'add_to_campaign'] as const) {
      draft = switchOn(draft, kind);
    }
    draft = {
      ...draft,
      steps: {
        ...draft.steps,
        add_to_campaign: { on: true, campaignId: CAMPAIGN },
        qualify: { on: false, brief: 'left off', minScore: 70 },
      },
    };
    expect(check(draft)).toEqual({
      gaps: [],
      plan: {
        definition: {
          listId: PEOPLE,
          steps: [
            { type: 'find_emails' },
            { type: 'add_to_campaign', campaignId: CAMPAIGN },
            { type: 'write_hubspot' },
          ],
        },
        creditCap: 30,
      },
    });
  });

  it('names each missing field, and has no plan until the definition is complete', () => {
    const draft = switchOn(switchOn({ ...blankDraft(), creditCap: 101 }, 'find_people'), 'qualify');
    expect(check(draft)).toEqual({
      gaps: ['name', 'list', 'accounts', 'titles', 'brief', 'cap'],
      plan: null,
    });
    const unnamed = { ...fromAutomation(saved, SAVED), name: '  ' };
    expect(check(unnamed).gaps).toEqual(['name']);
    expect(check(unnamed).plan?.definition).toEqual(SAVED);
  });

  it('turns a saved automation into a draft that sends the same definition back', () => {
    const draft = fromAutomation({ ...saved, triggerKind: 'schedule', cadence: 'weekly' }, SAVED);
    expect(draft).toEqual({
      name: 'Lead Milano',
      trigger: { kind: 'schedule', cadence: 'weekly' },
      signal: { watchlistId: '', minScore: 100 },
      listId: PEOPLE,
      steps: {
        find_people: {
          on: true,
          accountsListId: ACCOUNTS,
          titlesText: 'Head of Sales\nCEO',
          savedTitles: ['Head of Sales', 'CEO'],
          seniorities: ['vp', 'junior', 'c_suite'],
          perAccount: 3,
        },
        qualify: { on: true, brief: 'B2B SaaS in Italy', minScore: 50 },
        find_emails: { on: false },
        add_to_campaign: { on: true, campaignId: CAMPAIGN },
        add_to_instantly: { on: false, campaignId: '' },
        write_hubspot: { on: false },
      },
      creditCap: 20,
    });
    expect(check(draft).plan).toEqual({ definition: SAVED, creditCap: 20 });
  });

  it('keeps the campaign of a saved Instantly step, so a save sends it back', () => {
    const definition: Definition = {
      listId: PEOPLE,
      steps: [{ type: 'find_emails' }, { type: 'add_to_instantly', campaignId: CAMPAIGN }],
    };
    const draft = fromAutomation(saved, definition);
    expect(draft.steps.add_to_instantly).toEqual({ on: true, campaignId: CAMPAIGN });
    expect(check(draft).plan).toEqual({ definition, creditCap: 20 });
  });

  it('asks for the campaign of an Instantly step', () => {
    const draft: Draft = { ...blankDraft(), name: 'Lead Milano', listId: PEOPLE };
    expect(check(switchOn(draft, 'add_to_instantly'))).toEqual({ gaps: ['campaign'], plan: null });
  });

  it('refuses a plan that adds people to a campaign and to Instantly, as the API does', () => {
    const draft: Draft = {
      ...blankDraft(),
      name: 'Lead Milano',
      listId: PEOPLE,
      steps: {
        ...blankDraft().steps,
        add_to_campaign: { on: true, campaignId: CAMPAIGN },
        add_to_instantly: { on: true, campaignId: CAMPAIGN },
      },
    };
    expect(check(draft)).toEqual({ gaps: ['oneSystem'], plan: null });
    const instantlyOnly = { ...draft.steps.add_to_campaign, on: false };
    expect(
      check({ ...draft, steps: { ...draft.steps, add_to_campaign: instantlyOnly } }).gaps,
    ).toEqual([]);
  });

  it('says the review run holds its people for Instantly, and that older holds go', () => {
    const instantly = { type: 'add_to_instantly', campaignId: CAMPAIGN } as const;
    const plan = { definition: { listId: PEOPLE, steps: [instantly] }, creditCap: 20 };
    const saved = { enabled: true, triggerKind: 'manual', plan, signal: null } as const;
    const manual = { kind: 'manual' } as const;
    expect(saveEffects(saved, manual, { ...plan, creditCap: 30 })).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewInstantly',
      'instantlyReleased',
    ]);
    const emailsOnly = {
      ...plan,
      definition: { listId: PEOPLE, steps: [{ type: 'find_emails' as const }] },
    };
    expect(saveEffects({ ...saved, plan: emailsOnly }, manual, plan)).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewInstantly',
    ]);
  });

  it('says that another Instantly campaign, or none, drops the holds that wait for a batch', () => {
    const instantly = { type: 'add_to_instantly', campaignId: CAMPAIGN } as const;
    const plan = { definition: { listId: PEOPLE, steps: [instantly] }, creditCap: 20 };
    const saved = { enabled: true, triggerKind: 'manual', plan, signal: null } as const;
    const manual = { kind: 'manual' } as const;
    const other = { ...instantly, campaignId: '00000000-0000-4000-8000-000000000099' };
    const replaced = saveEffects(saved, manual, {
      ...plan,
      definition: { listId: PEOPLE, steps: [other] },
    });
    expect(replaced).toContain('instantlyCampaignDropped');
    expect(replaced).not.toContain('instantlyReleased');
    const removed = saveEffects(saved, manual, {
      ...plan,
      definition: { listId: PEOPLE, steps: [{ type: 'find_emails' }] },
    });
    expect(removed).toContain('instantlyCampaignDropped');
    expect(removed).not.toContain('reviewInstantly');
  });

  it.each(['webhook', 'signal'] as const)(
    'keeps the %s trigger of a saved automation, so a save never sends it back as manual',
    (kind) => {
      const draft = fromAutomation({ ...saved, triggerKind: kind }, SAVED);
      expect(draft.trigger).toEqual({ kind });
      expect(check(draft).plan).toEqual({ definition: SAVED, creditCap: 20 });
    },
  );

  it('makes a new version for another cap or step, not for a rename or a trigger', () => {
    const before = { definition: SAVED, creditCap: 20 };
    const draft = fromAutomation(saved, SAVED);
    const planOf = (d: Draft) => {
      const { plan } = check(d);
      if (!plan) throw new Error('incomplete draft');
      return plan;
    };
    const minScore = { ...draft.steps.qualify, minScore: 60 };
    expect(makesNewVersion(before, planOf({ ...draft, creditCap: 25 }))).toBe(true);
    expect(
      makesNewVersion(before, planOf({ ...draft, steps: { ...draft.steps, qualify: minScore } })),
    ).toBe(true);
    expect(makesNewVersion(before, planOf({ ...draft, name: 'Lead Roma' }))).toBe(false);
    expect(
      makesNewVersion(
        before,
        planOf({ ...draft, trigger: { kind: 'schedule', cadence: 'daily' } }),
      ),
    ).toBe(false);
    const reordered = { creditCap: 20, definition: { steps: SAVED.steps, listId: PEOPLE } };
    expect(makesNewVersion(before, reordered)).toBe(false);
  });

  it('says what a save does to the runs', () => {
    const plan = { definition: SAVED, creditCap: 20 };
    const running = { enabled: true, triggerKind: 'manual', plan, signal: null } as const;
    const manual = { kind: 'manual' } as const;
    expect(saveEffects(running, manual, { ...plan, creditCap: 30 })).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewDrafts',
      'draftsRemoved',
    ]);
    const paused = { ...running, enabled: false } as const;
    const daily = { kind: 'schedule', cadence: 'daily' } as const;
    expect(saveEffects(paused, daily, plan)).toEqual(['scheduleOnResume']);
    expect(saveEffects({ ...paused, enabled: true }, daily, plan)).toEqual(['scheduleStarts']);
  });

  it('says that a switch to the webhook needs a secret and a switch away voids the URL', () => {
    const plan = { definition: SAVED, creditCap: 20 };
    const webhook = { kind: 'webhook' } as const;
    const manual = { kind: 'manual' } as const;
    const scheduled = { enabled: true, triggerKind: 'schedule', plan, signal: null } as const;
    expect(saveEffects(scheduled, webhook, plan)).toEqual(['queuedStillRuns', 'webhookSecretNext']);
    expect(saveEffects({ ...scheduled, triggerKind: 'webhook' }, manual, plan)).toEqual([
      'webhookStops',
    ]);
    expect(saveEffects({ ...scheduled, triggerKind: 'webhook' }, webhook, plan)).toEqual([]);
  });

  it('says that a switch away from the signals removes the watchlist and cancels a queued run', () => {
    const plan = { definition: SAVED, creditCap: 20 };
    const signal = { enabled: true, triggerKind: 'signal', plan, signal: savedSignal } as const;
    expect(saveEffects(signal, { kind: 'manual' }, plan)).toEqual([
      'signalStops',
      'queuedCancelled',
    ]);
    expect(saveEffects(signal, { kind: 'webhook' }, plan)).toEqual([
      'webhookSecretNext',
      'signalStops',
      'queuedCancelled',
    ]);
    expect(saveEffects(signal, onWatchlist, plan)).toEqual([]);
  });

  it('says once that a new version that leaves the signals cancels a queued run', () => {
    const signal = {
      enabled: true,
      triggerKind: 'signal',
      plan: { definition: SAVED, creditCap: 20 },
      signal: savedSignal,
    } as const;
    const effects = saveEffects(signal, { kind: 'manual' }, { definition: SAVED, creditCap: 30 });
    expect(effects).toContain('signalStops');
    expect(effects.filter((effect) => effect === 'queuedCancelled')).toHaveLength(1);
  });

  it('says that only signals after the save count when the watchlist is new, not when the minimum moves', () => {
    const plan = { definition: SAVED, creditCap: 20 };
    const manual = { enabled: true, triggerKind: 'manual', plan, signal: null } as const;
    expect(saveEffects(manual, onWatchlist, plan)).toEqual(['signalFromNow']);
    const signal = { enabled: true, triggerKind: 'signal', plan, signal: savedSignal } as const;
    const other = { ...onWatchlist, watchlistId: ACCOUNTS };
    expect(saveEffects(signal, other, plan)).toEqual(['signalFromNow', 'queuedCancelled']);
    expect(saveEffects(signal, { ...onWatchlist, minScore: 150 }, plan)).toEqual([]);
  });

  it('reads a saved signal trigger, and previews a deleted watchlist as none for the API to name', () => {
    const draft = fromAutomation({ ...saved, triggerKind: 'signal', signal: savedSignal }, SAVED);
    expect(triggerBody(draft)).toEqual(onWatchlist);
    const gone = { ...savedSignal, watchlistId: null, watchlistName: null };
    const deleted = fromAutomation({ ...saved, triggerKind: 'signal', signal: gone }, SAVED);
    expect(triggerBody(deleted)).toEqual({ ...onWatchlist, watchlistId: null });
    expect(check(deleted).plan).not.toBeNull();
  });

  it('keeps the signal fields while another trigger is picked, and checks the minimum only for signals', () => {
    const draft = fromAutomation({ ...saved, triggerKind: 'signal', signal: savedSignal }, SAVED);
    const manual = { ...draft, trigger: { kind: 'manual' } } as const;
    expect(triggerBody(manual)).toEqual({ kind: 'manual' });
    expect(triggerBody({ ...manual, trigger: { kind: 'signal' } })).toEqual(onWatchlist);
    const zero = { ...draft, signal: { ...draft.signal, minScore: 0 } };
    expect(check(zero).gaps).toEqual(['signalScore']);
    expect(check({ ...zero, trigger: { kind: 'manual' } }).gaps).toEqual([]);
    expect(check({ ...draft, signal: { ...draft.signal, minScore: 75.5 } }).gaps).toEqual([]);
    expect(check({ ...draft, signal: { ...draft.signal, minScore: 10001 } }).gaps).toEqual([
      'signalScore',
    ]);
  });

  it('starts a new automation with no watchlist and a minimum of one fresh posting', () => {
    expect(blankDraft().signal).toEqual({ watchlistId: '', minScore: 100 });
  });

  it('sends titles back as the API stored them until the titles are edited', () => {
    const odd: Definition = {
      listId: PEOPLE,
      steps: [
        {
          type: 'find_people',
          accountsListId: ACCOUNTS,
          titles: ['Head\nof Sales', '  '],
          seniorities: [],
          perAccount: 3,
        },
      ],
    };
    const draft = fromAutomation(saved, odd);
    const renamed = check({ ...draft, name: 'Lead Roma' }).plan;
    expect(renamed?.definition.steps).toEqual(odd.steps);
    expect(renamed && makesNewVersion({ definition: odd, creditCap: 20 }, renamed)).toBe(false);
    const find = { ...draft.steps.find_people, titlesText: 'CEO\n\nCTO' };
    const edited = check({ ...draft, steps: { ...draft.steps, find_people: find } }).plan;
    expect(edited?.definition.steps).toEqual([{ ...odd.steps[0], titles: ['CEO', 'CTO'] }]);
  });

  it('says what the review run of a new version holds back, from the steps of the new plan', () => {
    const before = { definition: SAVED, creditCap: 20 };
    const saved = { enabled: true, triggerKind: 'manual', plan: before, signal: null } as const;
    const manual = { kind: 'manual' } as const;
    const withSteps = (...steps: Definition['steps']) => ({
      definition: { listId: PEOPLE, steps },
      creditCap: 20,
    });
    const campaign = { type: 'add_to_campaign', campaignId: CAMPAIGN } as const;
    const hubspot = { type: 'write_hubspot' } as const;
    expect(saveEffects(saved, manual, withSteps(hubspot))).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewHubspot',
      'draftsRemoved',
    ]);
    expect(saveEffects(saved, manual, withSteps(campaign, hubspot))).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewDrafts',
      'reviewHubspot',
      'draftsRemoved',
    ]);
    expect(saveEffects(saved, manual, withSteps({ type: 'find_emails' }))).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'draftsRemoved',
    ]);
    const noCampaign = { ...saved, plan: withSteps({ type: 'find_emails' }) };
    expect(saveEffects(noCampaign, manual, withSteps(hubspot))).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewHubspot',
    ]);
    expect(
      saveEffects(noCampaign, manual, withSteps({ type: 'qualify', brief: 'x', minScore: 50 })),
    ).toEqual(['newVersion', 'queuedCancelled', 'runningFinishes', 'nextReview']);
  });

  it('says a run the schedule queued still runs after a switch to manual', () => {
    const plan = { definition: SAVED, creditCap: 20 };
    const scheduled = { enabled: true, triggerKind: 'schedule', plan, signal: null } as const;
    const manual = { kind: 'manual' } as const;
    expect(saveEffects(scheduled, manual, plan)).toEqual(['queuedStillRuns']);
    expect(saveEffects({ ...scheduled, enabled: false }, manual, plan)).toEqual([]);
    expect(saveEffects(scheduled, manual, { ...plan, creditCap: 30 })).toEqual([
      'newVersion',
      'queuedCancelled',
      'runningFinishes',
      'nextReview',
      'reviewDrafts',
      'draftsRemoved',
    ]);
  });
});
