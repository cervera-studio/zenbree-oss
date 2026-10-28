// @vitest-environment happy-dom
import { createTranslator } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { FORMATS, type Locale } from '@/i18n/config';
import type { AutomationRunDetail } from '@/lib/automations/api';
import { MESSAGES } from '@/messages';
import { RunNow } from './automation-controls';
import { RunActions } from './run-actions';
import { RunFacts } from './run-detail';

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/automations/api', () => ({
  getRuns: vi.fn(),
  runAutomation: vi.fn(),
  setEnabled: vi.fn(),
  cancelRun: vi.fn(),
  approveRun: vi.fn(),
}));

type Kind = 'add_to_campaign' | 'write_hubspot';
const COMBOS: { kinds: Kind[] }[] = [
  { kinds: [] },
  { kinds: ['add_to_campaign'] },
  { kinds: ['write_hubspot'] },
  { kinds: ['add_to_campaign', 'write_hubspot'] },
];
const CAMPAIGN_WORD: Record<Locale, RegExp> = {
  en: /campaign/i,
  it: /campagna/i,
  es: /campaña/i,
  fr: /campagne/i,
};

const USER = '00000000-0000-4000-8000-000000000001';
const definition = (kinds: Kind[]) => ({
  listId: 'l1',
  steps: kinds.map((type) => (type === 'add_to_campaign' ? { type, campaignId: 'c1' } : { type })),
});
const automation = (kinds: Kind[]) => ({
  id: 'a1',
  name: 'Lead',
  enabled: true,
  creditCap: 40,
  version: 2,
  approvedVersion: 1,
  userId: USER,
  triggerKind: 'manual' as const,
  definition: definition(kinds),
});
const run = (status: AutomationRunDetail['status'], kinds: Kind[]): AutomationRunDetail => ({
  id: 'r1',
  automationId: 'a1',
  version: 2,
  triggerKind: 'manual',
  status,
  cancelRequested: false,
  creditCap: 40,
  creditsSpent: 0,
  review: true,
  approvedAt: null,
  summary: {},
  errorCode: null,
  createdAt: '2026-10-16T10:00:00Z',
  startedAt: null,
  finishedAt: null,
  delivery: null,
  instantly: null,
  signal: null,
  steps: kinds.map((kind, position) => ({
    position,
    kind,
    status: 'succeeded',
    decision: null,
    input: {},
    output: {},
    credits: 0,
    attempts: 1,
    errorCode: null,
    startedAt: null,
    finishedAt: null,
  })),
});

const label = (kinds: Kind[]) => kinds.join('+') || 'neither';
const expectNames = (text: string, kinds: Kind[]) => {
  expect(/hubspot/i.test(text)).toBe(kinds.includes('write_hubspot'));
  expect(/campagna/i.test(text)).toBe(kinds.includes('add_to_campaign'));
};

beforeEach(() => vi.clearAllMocks());

describe.each(COMBOS)('the review texts of a definition with %j', ({ kinds }) => {
  it(`confirm-run dialog (${label(kinds)})`, async () => {
    const c = await render(<RunNow automation={automation(kinds)} label="runNow" />);
    await click(control(c, 'Esegui ora'));
    expectNames(document.querySelector('[role="alertdialog"]')?.textContent ?? '', kinds);
  });

  it(`run page banner (${label(kinds)})`, async () => {
    const c = await render(<RunFacts run={run('queued', [])} automation={automation(kinds)} />);
    expectNames(c.querySelector('p.bg-warning-tint')?.textContent ?? '', kinds);
  });

  it(`approve dialog (${label(kinds)})`, async () => {
    const c = await render(
      <RunActions
        run={run('succeeded', kinds)}
        automation={automation(kinds)}
        viewer={{ id: USER, isOrgAdmin: false }}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    await click(control(c, 'Approva esecuzione'));
    expectNames(document.querySelector('[role="alertdialog"]')?.textContent ?? '', kinds);
  });
});

describe.each(Object.keys(MESSAGES) as Locale[])('the review keys in %s', (locale) => {
  const t = createTranslator({
    locale,
    messages: MESSAGES[locale],
    namespace: 'automations',
    formats: FORMATS,
  });
  const keys = ['confirmRun.review', 'run.reviewText', 'run.approveText'] as const;

  it.each(COMBOS)('name HubSpot and the campaign only when the run has them: %j', ({ kinds }) => {
    for (const key of keys) {
      const text = t(key, {
        version: 2,
        hubspot: kinds.includes('write_hubspot') ? 'yes' : 'no',
        campaign: kinds.includes('add_to_campaign') ? 'yes' : 'no',
      });
      expect(text.length).toBeGreaterThan(0);
      expect(/hubspot/i.test(text)).toBe(kinds.includes('write_hubspot'));
      expect(CAMPAIGN_WORD[locale].test(text)).toBe(kinds.includes('add_to_campaign'));
    }
  });
});

describe.each(Object.keys(MESSAGES) as Locale[])('the stop keys in %s', (locale) => {
  const t = createTranslator({
    locale,
    messages: MESSAGES[locale],
    namespace: 'automations',
    formats: FORMATS,
  });

  it.each(['run.cancelRunning', 'run.stopping'] as const)(
    '%s names HubSpot only for a run with it',
    (key) => {
      const withHubspot = t(key, { hubspot: 'yes' });
      const without = t(key, { hubspot: 'no' });
      expect(/hubspot/i.test(withHubspot)).toBe(true);
      expect(/hubspot/i.test(without)).toBe(false);
      expect(without).toMatch(/[.]$/);
      expect(without).not.toMatch(/\s{2}|[.][^\s]/);
    },
  );
});

describe('the stop text on the run page', () => {
  it.each(COMBOS)('names HubSpot only for a run with the step: %j', async ({ kinds }) => {
    const live = { ...run('running', kinds), cancelRequested: true };
    const c = await render(<RunFacts run={live} automation={automation(kinds)} />);
    const text = c.querySelector('p[role="status"]')?.textContent ?? '';
    expect(text.length).toBeGreaterThan(0);
    expect(/hubspot/i.test(text)).toBe(kinds.includes('write_hubspot'));
  });
});
