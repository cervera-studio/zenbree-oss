// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { AutomationRunDetail, RunStep } from '@/lib/automations/api';
import { AutomationRuns } from './automation-runs';
import { RunFacts, RunSummary } from './run-detail';

const charged = (position: number, credits: number): RunStep => ({
  position,
  kind: 'qualify',
  status: 'succeeded',
  decision: null,
  input: {},
  output: {},
  credits,
  attempts: 1,
  errorCode: null,
  startedAt: null,
  finishedAt: null,
});

const run = (overrides: Partial<AutomationRunDetail>): AutomationRunDetail => ({
  id: '00000000-0000-4000-8000-0000000000b1',
  automationId: '00000000-0000-4000-8000-0000000000a1',
  version: 1,
  triggerKind: 'schedule',
  status: 'succeeded',
  cancelRequested: false,
  creditCap: 40,
  creditsSpent: 6,
  review: false,
  approvedAt: null,
  summary: {},
  errorCode: null,
  createdAt: '2026-10-16T10:00:00Z',
  startedAt: '2026-10-16T10:00:05Z',
  finishedAt: '2026-10-16T10:03:00Z',
  steps: [],
  delivery: null,
  instantly: null,
  signal: null,
  ...overrides,
});

// The reaper closes a dead run without writing `credits_spent`; its steps recorded what they charged.
const dead = run({
  id: '00000000-0000-4000-8000-0000000000b2',
  status: 'unknown',
  errorCode: 'worker_outcome_unknown',
  creditsSpent: 0,
  steps: [charged(0, 3), charged(1, 1)],
});

describe('the credits of a run', () => {
  it('counts what the steps of a dead run charged, and says why it ended', async () => {
    const c = await render(
      <RunFacts
        run={dead}
        automation={{ approvedVersion: 1, definition: { listId: 'l1', steps: [] } }}
      />,
    );
    expect(c.textContent).toContain('Crediti spesi4 di 40 crediti');
    expect(c.textContent).toContain(
      "L'esecuzione si è fermata senza dare un esito. I crediti non usati dai suoi passi sono tornati indietro.",
    );
  });

  it('names the people a stop or Instantly kept out before any credit', async () => {
    const taken = { people: 1, refusedBeforeAnyCredit: { stopped: 2, heldByInstantly: 1 } };
    const c = await render(<RunSummary run={run({ summary: { taken } })} />);
    expect(c.textContent).toContain('Fermate dopo una risposta o una disiscrizione2');
    expect(c.textContent).toContain('Già su Instantly1');
  });

  it('counts the drafts an approval made active only for a run that adds to a campaign', async () => {
    const step = (kind: RunStep['kind']) => ({ ...charged(0, 0), kind });
    const summary = { taken: { people: 4 }, promoted: 0 };
    const instantly = await render(
      <RunSummary run={run({ summary, steps: [step('add_to_instantly')] })} />,
    );
    expect(instantly.textContent).toContain('Prese in carico da questa esecuzione4');
    expect(instantly.textContent).not.toContain("Rese attive dall'approvazione");
    const native = await render(
      <RunSummary run={run({ summary, steps: [step('add_to_campaign')] })} />,
    );
    expect(native.textContent).toContain("Rese attive dall'approvazione0");
  });

  it('shows the closing total only for a run that closed normally', async () => {
    const c = await render(
      <AutomationRuns
        automation={{ id: dead.automationId, approvedVersion: 1 }}
        runs={[run({}), dead]}
      />,
    );
    const rows = [...c.querySelectorAll('tbody tr')].map((tr) => tr.textContent);
    expect(rows[0]).toContain('6 di 40 crediti');
    expect(rows[1]).toContain("Vedi l'esecuzione");
    expect(rows[1]).not.toContain('0 di 40');
  });

  it('tags a queued run of an unapproved version for review, and not one cancelled before it started', async () => {
    const c = await render(
      <AutomationRuns
        automation={{ id: dead.automationId, approvedVersion: 0 }}
        runs={[
          run({ status: 'queued', review: null, startedAt: null, finishedAt: null }),
          run({ status: 'cancelled', review: null, errorCode: 'cancelled', startedAt: null }),
        ]}
      />,
    );
    const rows = [...c.querySelectorAll('tbody tr')].map((tr) => tr.textContent);
    expect(rows[0]).toContain('Da rivedere');
    expect(rows[1]).not.toContain('Da rivedere');
  });
});

describe('the review banner of an Instantly run', () => {
  const definition = { listId: 'l1', steps: [] };
  const step = (kind: RunStep['kind']) => ({ ...charged(0, 0), kind });
  const campaign = { id: 'c1', name: 'Verify running', status: 1, maySend: true };
  const holding = { people: 2, batchId: null, waitingFor: 'approval' as const, campaign };
  const banner = async (subject: AutomationRunDetail) => {
    const c = await render(
      <RunFacts run={subject} automation={{ approvedVersion: null, definition }} />,
    );
    return c.querySelector('p.bg-warning-tint')?.textContent;
  };

  it.each([
    [
      [step('add_to_instantly')],
      "Le persone di questa esecuzione restano in attesa: non vengono aggiunte a Instantly finché non approvi l'esecuzione.",
    ],
    [
      [step('add_to_instantly'), step('write_hubspot')],
      "Le persone di questa esecuzione restano in attesa: non vengono aggiunte a Instantly né scritte su HubSpot finché non approvi l'esecuzione.",
    ],
  ])('says the held people wait, naming HubSpot only with its step', async (steps, text) => {
    expect(await banner(run({ review: true, steps, instantly: holding }))).toBe(text);
  });

  it('claims no held people when the run holds nobody', async () => {
    expect(await banner(run({ review: true, steps: [step('add_to_instantly')] }))).toBe(
      "Questa esecuzione non ha nessuno da aggiungere a Instantly. Dopo l'approvazione, le esecuzioni successive di questa versione aggiungono le persone a Instantly senza revisione.",
    );
  });

  it.each([
    ['failed', 'instantly_refused'],
    ['cancelled', 'cancelled'],
  ] as const)(
    'offers no approval on a review run that %s, and claims nobody',
    async (status, errorCode) => {
      const ended = run({ review: true, status, errorCode, steps: [step('add_to_instantly')] });
      expect(await banner(ended)).toBeUndefined();
      const withHubspot = { ...ended, steps: [step('add_to_instantly'), step('write_hubspot')] };
      expect(await banner(withHubspot)).toBeUndefined();
    },
  );

  it('keeps the review banner of a native run that failed after adding people', async () => {
    const native = run({
      review: true,
      status: 'failed',
      steps: [{ ...step('add_to_campaign'), output: { enrolled: 1 } }],
    });
    expect(await banner(native)).toBe(
      "Le persone aggiunte da questa esecuzione restano nella campagna come bozze finché non approvi l'esecuzione.",
    );
  });

  it('reads the saved definition while the run is queued, and claims nobody yet', async () => {
    const queued = run({ review: null, status: 'queued', steps: [] });
    const c = await render(
      <RunFacts
        run={queued}
        automation={{
          approvedVersion: null,
          definition: { listId: 'l1', steps: [{ type: 'add_to_instantly', campaignId: 'c1' }] },
        }}
      />,
    );
    expect(c.querySelector('p.bg-warning-tint')?.textContent).toBe(
      "Le persone di questa esecuzione restano in attesa: non vengono aggiunte a Instantly finché non approvi l'esecuzione.",
    );
  });
});
