import type { Automation } from './api';
import {
  type Definition,
  type DefinitionStep,
  MAX_CREDIT_CAP,
  ORDER,
  type StepKind,
} from './definition';
import { type SignalFields, signalFieldsOf, signalScoreOk } from './signal-draft';

export type Trigger =
  | { kind: 'manual' }
  | { kind: 'schedule'; cadence: 'daily' | 'weekly' }
  | { kind: 'webhook' }
  | { kind: 'signal' };

export type TriggerBody =
  | Exclude<Trigger, { kind: 'signal' }>
  | { kind: 'signal'; watchlistId: string | null; minScore: number };

type Fields = {
  find_people: {
    accountsListId: string;
    titlesText: string;
    // The API keeps a title as it was sent, also one with a line break or only spaces.
    savedTitles: string[];
    seniorities: string[];
    perAccount: number;
  };
  qualify: { brief: string; minScore: number };
  find_emails: object;
  add_to_campaign: { campaignId: string };
  add_to_instantly: { campaignId: string };
  write_hubspot: object;
};

export type StepDrafts = { [K in StepKind]: { on: boolean } & Fields[K] };

export type Draft = {
  name: string;
  trigger: Trigger;
  signal: SignalFields;
  listId: string;
  steps: StepDrafts;
  creditCap: number;
};

export type Plan = { definition: Definition; creditCap: number };

export type Gap =
  | 'name'
  | 'list'
  | 'noStep'
  | 'accounts'
  | 'titles'
  | 'perAccount'
  | 'brief'
  | 'minScore'
  | 'campaign'
  | 'oneSystem'
  | 'signalScore'
  | 'cap';

const MAX_TITLES = 10;
const MAX_TITLE = 200;
const MAX_BRIEF = 1000;
const MAX_NAME = 120;

const OFF: StepDrafts = {
  find_people: {
    on: false,
    accountsListId: '',
    titlesText: '',
    savedTitles: [],
    seniorities: [],
    perAccount: 3,
  },
  qualify: { on: false, brief: '', minScore: 50 },
  find_emails: { on: false },
  add_to_campaign: { on: false, campaignId: '' },
  add_to_instantly: { on: false, campaignId: '' },
  write_hubspot: { on: false },
};

export function blankDraft(): Draft {
  return {
    name: '',
    trigger: { kind: 'manual' },
    signal: signalFieldsOf(null),
    listId: '',
    steps: OFF,
    creditCap: 10,
  };
}

export function triggerBody({ trigger, signal }: Pick<Draft, 'trigger' | 'signal'>): TriggerBody {
  if (trigger.kind !== 'signal') return trigger;
  return { kind: 'signal', watchlistId: signal.watchlistId || null, minScore: signal.minScore };
}

// A saved webhook or signal trigger stays one: a save that sent `manual` would delete its hook or its watchlist.
function triggerOf(saved: Pick<Automation, 'triggerKind' | 'cadence'>): Trigger {
  if (saved.triggerKind === 'webhook' || saved.triggerKind === 'signal')
    return { kind: saved.triggerKind };
  return saved.triggerKind === 'schedule' && saved.cadence
    ? { kind: 'schedule', cadence: saved.cadence }
    : { kind: 'manual' };
}

export function fromAutomation(
  saved: Pick<Automation, 'name' | 'triggerKind' | 'cadence' | 'creditCap' | 'signal'>,
  definition: Definition,
): Draft {
  const steps = { ...OFF };
  for (const step of definition.steps) {
    switch (step.type) {
      case 'find_people':
        steps.find_people = {
          on: true,
          accountsListId: step.accountsListId,
          titlesText: step.titles.join('\n'),
          savedTitles: step.titles,
          seniorities: step.seniorities,
          perAccount: step.perAccount,
        };
        break;
      case 'qualify':
        steps.qualify = { on: true, brief: step.brief, minScore: step.minScore };
        break;
      case 'add_to_campaign':
        steps.add_to_campaign = { on: true, campaignId: step.campaignId };
        break;
      case 'add_to_instantly':
        steps.add_to_instantly = { on: true, campaignId: step.campaignId };
        break;
      default:
        steps[step.type] = { on: true };
    }
  }
  return {
    name: saved.name,
    trigger: triggerOf(saved),
    signal: signalFieldsOf(saved),
    listId: definition.listId,
    steps,
    creditCap: saved.creditCap,
  };
}

function titlesOf({ titlesText, savedTitles }: StepDrafts['find_people']): string[] {
  if (titlesText === savedTitles.join('\n')) return savedTitles;
  return titlesText.split('\n').filter((line) => line.trim() !== '');
}
const within = (n: number, min: number, max: number) => Number.isInteger(n) && n >= min && n <= max;

function stepGaps(steps: StepDrafts): Gap[] {
  const gaps: Gap[] = [];
  const find = steps.find_people;
  if (find.on) {
    const titles = titlesOf(find);
    if (!find.accountsListId) gaps.push('accounts');
    if (
      titles.length === 0 ||
      titles.length > MAX_TITLES ||
      titles.some((t) => t.length > MAX_TITLE)
    )
      gaps.push('titles');
    if (!within(find.perAccount, 1, 5)) gaps.push('perAccount');
  }
  const qualify = steps.qualify;
  if (qualify.on) {
    if (!qualify.brief.trim() || qualify.brief.length > MAX_BRIEF) gaps.push('brief');
    if (!within(qualify.minScore, 0, 100)) gaps.push('minScore');
  }
  const native = steps.add_to_campaign;
  const instantly = steps.add_to_instantly;
  if ((native.on && !native.campaignId) || (instantly.on && !instantly.campaignId))
    gaps.push('campaign');
  // `Definition` refuses both: a run adds its people to one system.
  if (native.on && instantly.on) gaps.push('oneSystem');
  return gaps;
}

function toStep(kind: StepKind, steps: StepDrafts): DefinitionStep {
  switch (kind) {
    case 'find_people': {
      const { accountsListId, seniorities, perAccount } = steps.find_people;
      return {
        type: kind,
        accountsListId,
        titles: titlesOf(steps.find_people),
        seniorities,
        perAccount,
      };
    }
    case 'qualify':
      return { type: kind, brief: steps.qualify.brief, minScore: steps.qualify.minScore };
    case 'add_to_campaign':
      return { type: kind, campaignId: steps.add_to_campaign.campaignId };
    case 'add_to_instantly':
      return { type: kind, campaignId: steps.add_to_instantly.campaignId };
    default:
      return { type: kind };
  }
}

export function check(draft: Draft): { gaps: Gap[]; plan: Plan | null } {
  const on = ORDER.filter((kind) => draft.steps[kind].on);
  const planGaps: Gap[] = [
    ...(draft.listId ? [] : (['list'] as const)),
    ...(on.length === 0 ? (['noStep'] as const) : []),
    ...stepGaps(draft.steps),
    ...(draft.trigger.kind === 'signal' && !signalScoreOk(draft.signal.minScore)
      ? (['signalScore'] as const)
      : []),
    ...(within(draft.creditCap, 1, MAX_CREDIT_CAP) ? [] : (['cap'] as const)),
  ];
  const name = draft.name.trim();
  const gaps: Gap[] = [
    ...(name && name.length <= MAX_NAME ? [] : (['name'] as const)),
    ...planGaps,
  ];
  if (planGaps.length > 0) return { gaps, plan: null };
  return {
    gaps,
    plan: {
      definition: { listId: draft.listId, steps: on.map((kind) => toStep(kind, draft.steps)) },
      creditCap: draft.creditCap,
    },
  };
}

// The API compares the stored JSON: the order of keys doesn't count, the order of a list does.
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([key, v]) => [key, canonical(v)]),
    );
  }
  return value;
}

export const planKey = (plan: Plan) => JSON.stringify(canonical(plan));

/** As `service._changes` decides: only another definition or cap makes a new version, not a name or trigger. */
export function makesNewVersion(saved: Plan, plan: Plan): boolean {
  return planKey(saved) !== planKey(plan);
}

export type Effect =
  | 'newVersion'
  | 'queuedCancelled'
  | 'runningFinishes'
  | 'nextReview'
  | 'reviewDrafts'
  | 'reviewHubspot'
  | 'reviewInstantly'
  | 'draftsRemoved'
  | 'instantlyReleased'
  | 'instantlyCampaignDropped'
  | 'scheduleStarts'
  | 'scheduleOnResume'
  | 'queuedStillRuns'
  | 'webhookSecretNext'
  | 'webhookStops'
  | 'signalStops'
  | 'signalFromNow';

const instantlyCampaign = ({ definition }: Plan) =>
  definition.steps.flatMap((s) => (s.type === 'add_to_instantly' ? [s.campaignId] : []))[0];

type SavedTrigger = Pick<Automation, 'enabled' | 'triggerKind' | 'signal'>;

export function saveEffects(
  saved: SavedTrigger & { plan: Plan },
  trigger: TriggerBody,
  plan: Plan,
): Effect[] {
  const effects: Effect[] = [];
  const versioned = makesNewVersion(saved.plan, plan);
  const has = (kind: StepKind, { definition }: Plan) =>
    definition.steps.some((s) => s.type === kind);
  if (versioned) {
    effects.push('newVersion');
    // A run can be queued or start after the page loaded, so these lines never read the page's last run.
    effects.push('queuedCancelled', 'runningFinishes');
    effects.push('nextReview');
    if (has('add_to_campaign', plan)) effects.push('reviewDrafts');
    if (has('write_hubspot', plan)) effects.push('reviewHubspot');
    if (has('add_to_instantly', plan)) effects.push('reviewInstantly');
    // Each bump discards the drafts and holds of older versions, so only the saved version's runs hold any.
    if (has('add_to_campaign', saved.plan)) effects.push('draftsRemoved');
    if (
      instantlyCampaign(saved.plan) &&
      instantlyCampaign(saved.plan) !== instantlyCampaign(plan)
    ) {
      // Another campaign, or none, also drops the approved holds that wait for their batch.
      effects.push('instantlyCampaignDropped');
    } else if (has('add_to_instantly', saved.plan)) effects.push('instantlyReleased');
  }
  effects.push(...triggerEffects(saved, trigger, versioned));
  return effects;
}

/** What a save that changes the trigger does to the old trigger's state and the new one's first run. */
function triggerEffects(saved: SavedTrigger, trigger: TriggerBody, versioned: boolean): Effect[] {
  const effects: Effect[] = [];
  const next = trigger.kind;
  // `_changes` clears `next_run_at` and cancels nothing. A new version or a pause already cancels.
  const unscheduled = next !== 'schedule' && saved.triggerKind === 'schedule';
  if (unscheduled && saved.enabled && !versioned) effects.push('queuedStillRuns');
  // `_changes` queues the first scheduled run at once, also while paused: it is due when resumed.
  if (next === 'schedule' && saved.triggerKind !== 'schedule') {
    effects.push(saved.enabled ? 'scheduleStarts' : 'scheduleOnResume');
  }
  // The secret is made on the automation's page, once the saved trigger is the webhook.
  if (next === 'webhook' && saved.triggerKind !== 'webhook') {
    effects.push('webhookSecretNext');
  }
  // `service.update` deletes the hook and drops the calls that wait, under the same lock.
  if (next !== 'webhook' && saved.triggerKind === 'webhook') {
    effects.push('webhookStops');
  }
  // `service.update` moves `since` and cancels the queued run when the watchlist changes, and clears both when the
  // trigger leaves the signals. A new minimum keeps them.
  const otherWatchlist =
    saved.triggerKind === 'signal' &&
    trigger.kind === 'signal' &&
    (saved.signal?.watchlistId ?? null) !== trigger.watchlistId;
  if (next === 'signal' && (saved.triggerKind !== 'signal' || otherWatchlist)) {
    effects.push('signalFromNow');
  }
  if (next !== 'signal' && saved.triggerKind === 'signal') effects.push('signalStops');
  const leaves = saved.triggerKind === 'signal' && (next !== 'signal' || otherWatchlist);
  if (leaves && !versioned) effects.push('queuedCancelled'); // a new version already says it
  return effects;
}
