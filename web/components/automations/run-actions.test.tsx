// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, pressEscape, render } from '@/app/auth/testing';
import type { Automation, AutomationRunDetail, RunStep } from '@/lib/automations/api';
import { cancelRun, getRuns, runAutomation, setEnabled } from '@/lib/automations/api';
import { AppError, fail, succeed } from '@/lib/types';
import { AutomationControls } from './automation-controls';
import { RunActions } from './run-actions';

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/automations/api', () => ({
  getRuns: vi.fn(),
  runAutomation: vi.fn(),
  setEnabled: vi.fn(),
  cancelRun: vi.fn(),
  approveRun: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

const CREATOR = '00000000-0000-4000-8000-000000000001';
const MEMBER = '00000000-0000-4000-8000-000000000002';
const AUTOMATION_ID = '00000000-0000-4000-8000-0000000000a1';

const automation = {
  id: AUTOMATION_ID,
  name: 'Lead Milano',
  enabled: true,
  creditCap: 40,
  version: 2,
  approvedVersion: 1,
  userId: CREATOR,
  triggerKind: 'manual',
  definition: { listId: '00000000-0000-4000-8000-000000000011', steps: [] },
} satisfies Pick<
  Automation,
  | 'id'
  | 'name'
  | 'enabled'
  | 'creditCap'
  | 'version'
  | 'approvedVersion'
  | 'userId'
  | 'triggerKind'
  | 'definition'
>;

const run = (overrides: Partial<AutomationRunDetail> = {}): AutomationRunDetail => ({
  id: '00000000-0000-4000-8000-0000000000b1',
  automationId: AUTOMATION_ID,
  version: 2,
  triggerKind: 'manual',
  status: 'succeeded',
  cancelRequested: false,
  creditCap: 40,
  creditsSpent: 6,
  review: true,
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

const creator = { id: CREATOR, isOrgAdmin: false };
const buttons = (root: ParentNode) =>
  [...root.querySelectorAll('button')].map((b) => b.textContent?.trim());

function dialog() {
  const el = document.querySelector<HTMLElement>('[role="alertdialog"]');
  if (!el) throw new Error('no dialog');
  return el;
}

describe('who manages an automation', () => {
  it.each([
    ['its creator', ['Metti in pausa', 'Esegui ora'], creator],
    ['an admin', ['Metti in pausa', 'Esegui ora'], { id: MEMBER, isOrgAdmin: true }],
    ['another member', [], { id: MEMBER, isOrgAdmin: false }],
  ])('%s sees the head controls %j', async (_, expected, viewer) => {
    const c = await render(<AutomationControls automation={automation} viewer={viewer} />);
    expect(buttons(c)).toEqual(expected);
  });

  it('shows another member no run actions, and the creator all of them', async () => {
    const finished = run();
    const member = await render(
      <RunActions
        run={finished}
        automation={automation}
        viewer={{ id: MEMBER, isOrgAdmin: false }}
        campaignStatus="draft"
        instantlyStatus="none"
      />,
    );
    expect(buttons(member)).toEqual([]);
    const owner = await render(
      <RunActions
        run={finished}
        automation={automation}
        viewer={creator}
        campaignStatus="draft"
        instantlyStatus="none"
      />,
    );
    expect(buttons(owner)).toEqual(['Approva esecuzione', 'Esegui di nuovo']);
  });
});

describe.each(['webhook', 'signal'] as const)('a %s automation', (triggerKind) => {
  it('offers no Run now or Run again: only its calls or its signals start a run', async () => {
    const hooked = { ...automation, triggerKind };
    const head = await render(<AutomationControls automation={hooked} viewer={creator} />);
    expect(buttons(head)).toEqual(['Metti in pausa']);
    const ended = await render(
      <RunActions
        run={run({ triggerKind, review: false })}
        automation={hooked}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    expect(buttons(ended)).toEqual([]);
  });
});

describe('approving a run', () => {
  const enrolled = (n: number) => [
    {
      position: 0,
      kind: 'add_to_campaign' as const,
      status: 'succeeded' as const,
      decision: null,
      input: {},
      output: { enrolled: n, status: 'draft' },
      credits: 0,
      attempts: 1,
      errorCode: null,
      startedAt: null,
      finishedAt: null,
    },
  ];

  it.each([
    ['a finished review run', true, run()],
    ['a run that is not a review', false, run({ review: false })],
    ['a review run still going', false, run({ status: 'running', finishedAt: null })],
    ['an approved run', false, run({ approvedAt: '2026-10-16T11:00:00Z' })],
    ['a failed review run that added nobody', false, run({ status: 'failed' })],
    ['a dead review run that added people', true, run({ status: 'unknown', steps: enrolled(2) })],
    ['a review run of an older version', false, run({ version: 1 })],
  ] as const)('offers approval for %s: %s', async (_, offered, subject) => {
    const c = await render(
      <RunActions
        run={subject}
        automation={automation}
        viewer={creator}
        campaignStatus="draft"
        instantlyStatus="none"
      />,
    );
    expect(buttons(c).includes('Approva esecuzione')).toBe(offered);
  });

  it('tells only a manager to run an outdated review run again', async () => {
    const outdated = (viewer: { id: string; isOrgAdmin: boolean }) =>
      render(
        <RunActions
          run={run({ version: 1 })}
          automation={automation}
          viewer={viewer}
          campaignStatus="none"
          instantlyStatus="none"
        />,
      );
    const why = "L'automazione è cambiata dopo questa esecuzione, quindi non si può più approvare.";
    const again = 'Eseguila di nuovo per rivedere la nuova versione.';
    const owner = await outdated(creator);
    expect(owner.textContent).toContain(why);
    expect(owner.textContent).toContain(again);
    const member = await outdated({ id: MEMBER, isOrgAdmin: false });
    expect(member.textContent).toContain(why);
    expect(member.textContent).not.toContain(again);
  });

  it.each([
    ['active', 'La campagna è attiva.'],
    ['unreadable', 'Non è stato possibile controllare la campagna.'],
    ['draft', null],
  ] as const)('warns about a %s campaign before approval', async (status, warning) => {
    const c = await render(
      <RunActions
        run={run()}
        automation={automation}
        viewer={creator}
        campaignStatus={status}
        instantlyStatus="none"
      />,
    );
    await click(control(c, 'Approva esecuzione'));
    const text = dialog().textContent ?? '';
    expect(text.includes('la campagna può scrivere loro al prossimo passo')).toBe(warning !== null);
    if (warning) expect(text).toContain(warning);
  });
});

const step = (kind: RunStep['kind'], output: RunStep['output'] = {}): RunStep => ({
  position: 0,
  kind,
  status: 'succeeded',
  decision: null,
  input: {},
  output,
  credits: 0,
  attempts: 1,
  errorCode: null,
  startedAt: null,
  finishedAt: null,
});

const instantlyCampaign = { id: 'c1', name: 'Verify running', status: 1, maySend: true };

describe('approving a run of an Instantly automation', () => {
  const holding = run({
    steps: [step('add_to_instantly', { held: 2, campaign: instantlyCampaign })],
    instantly: { people: 2, batchId: null, waitingFor: 'approval', campaign: instantlyCampaign },
  });
  // The step found nobody to add, so the run holds nobody; its approval still opens later runs.
  const empty = run({
    steps: [
      step('add_to_instantly', { held: 0, skipped: { noEmail: 3 }, campaign: instantlyCampaign }),
    ],
  });

  async function approveDialog(
    subject: AutomationRunDetail,
    status: 'sends' | 'quiet' | 'unreadable',
  ) {
    const c = await render(
      <RunActions
        run={subject}
        automation={automation}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus={status}
      />,
    );
    await click(control(c, 'Approva esecuzione'));
    return dialog().textContent ?? '';
  }

  it.each([
    [
      'sends',
      "La campagna Instantly è attiva: dopo l'approvazione può iniziare subito a inviare email a queste persone e a quelle delle prossime esecuzioni.",
    ],
    [
      'unreadable',
      "Non riusciamo a verificare lo stato della campagna Instantly. Se è attiva, dopo l'approvazione può iniziare subito a inviare email a queste persone.",
    ],
    ['quiet', null],
  ] as const)('warns about a campaign read live as %s for held people', async (status, warning) => {
    const text = await approveDialog(holding, status);
    expect(text).toContain(
      'Approvando, le persone di questa esecuzione vengono aggiunte subito a Instantly. Le esecuzioni successive di questa versione le aggiungono senza attendere.',
    );
    expect(text.includes('inviare email')).toBe(warning !== null);
    if (warning) expect(text).toContain(warning);
  });

  it.each([
    [
      'sends',
      "La campagna Instantly è attiva: dopo l'approvazione può iniziare subito a inviare email alle persone delle prossime esecuzioni.",
    ],
    [
      'unreadable',
      "Non riusciamo a verificare lo stato della campagna Instantly. Se è attiva, dopo l'approvazione può iniziare subito a inviare email alle persone delle prossime esecuzioni.",
    ],
  ] as const)(
    'warns about later runs when the run holds nobody and the campaign is %s',
    async (status, warning) => {
      const text = await approveDialog(empty, status);
      expect(text).toContain(
        'Questa esecuzione non ha nessuno da aggiungere a Instantly. Approvando, le esecuzioni successive di questa versione aggiungono le persone senza attendere.',
      );
      expect(text).toContain(warning);
      expect(text).not.toContain('queste persone');
    },
  );

  it('names HubSpot only for a run that writes to it', async () => {
    const both = run({ ...holding, steps: [...holding.steps, step('write_hubspot')] });
    expect(await approveDialog(both, 'quiet')).toContain(
      'Le esecuzioni successive di questa versione le aggiungono senza attendere e le scrivono su HubSpot.',
    );
    expect(await approveDialog(holding, 'quiet')).not.toContain('HubSpot');
  });

  it.each([
    [
      [{ type: 'add_to_instantly', campaignId: 'c1' }],
      'È la prima esecuzione della versione 2, quindi va rivista. Nessuno viene aggiunto a Instantly finché non la approvi.',
    ],
    [
      [{ type: 'add_to_instantly', campaignId: 'c1' }, { type: 'write_hubspot' }],
      'È la prima esecuzione della versione 2, quindi va rivista. Nessuno viene aggiunto a Instantly né scritto su HubSpot finché non la approvi.',
    ],
  ] as const)(
    'says before a review run of %j that nobody is added before approval',
    async (steps, text) => {
      const instantly = {
        ...automation,
        definition: { listId: automation.definition.listId, steps: [...steps] },
      };
      const c = await render(<AutomationControls automation={instantly} viewer={creator} />);
      await click(control(c, 'Esegui ora'));
      expect(dialog().textContent).toContain(text);
      expect(dialog().textContent).not.toContain('bozze');
    },
  );
});

describe('writing held people to HubSpot after approval', () => {
  const held = [
    {
      position: 0,
      kind: 'write_hubspot' as const,
      status: 'succeeded' as const,
      decision: null,
      input: {},
      output: { heldForApproval: 3 },
      credits: 0,
      attempts: 1,
      errorCode: null,
      startedAt: null,
      finishedAt: null,
    },
  ];
  const approved = (summary: AutomationRunDetail['summary']) =>
    run({ approvedAt: '2026-10-16T11:00:00Z', steps: held, summary });
  const admin = { id: MEMBER, isOrgAdmin: true };
  const AGAIN = 'Scrivi di nuovo su HubSpot';

  it.each([
    ['failed', { hubspotAfterApproval: { errorCode: 'hubspot_failed' } }, 'non è finita'],
    ['not finished', {}, 'Nessuno viene scritto due volte'],
  ])('offers a %s write again while its creator exists', async (_, summary, text) => {
    const offered = await render(
      <RunActions
        run={approved(summary)}
        automation={automation}
        viewer={admin}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    expect(buttons(offered)).toContain(AGAIN);
    expect(offered.textContent).toContain(text);
    const orphan = await render(
      <RunActions
        run={approved(summary)}
        automation={{ ...automation, userId: null }}
        viewer={admin}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    expect(buttons(orphan)).not.toContain(AGAIN);
  });

  it('offers nothing once the write finished', async () => {
    const c = await render(
      <RunActions
        run={approved({ hubspotAfterApproval: { errorCode: null } })}
        automation={automation}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    expect(buttons(c)).toEqual(['Esegui di nuovo']);
  });

  it('offers nothing on an approved run that held nobody', async () => {
    const c = await render(
      <RunActions
        run={run({ approvedAt: '2026-10-16T11:00:00Z', steps: [], summary: {} })}
        automation={automation}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    expect(buttons(c)).toEqual(['Esegui di nuovo']);
  });
});

describe('cancelling a run', () => {
  const live = run({ status: 'running', finishedAt: null, review: false });

  it.each([
    ['a queued run', run({ status: 'cancelled', errorCode: 'cancelled' }), 'Esecuzione annullata.'],
    [
      'a running run',
      { ...live, cancelRequested: true },
      "L'esecuzione si ferma prima che il passo in corso salvi il risultato. Quel passo e i successivi vengono saltati e non addebitano nulla.",
    ],
    [
      'a run that ended before the click',
      run({ status: 'succeeded', review: false }),
      "L'esecuzione era già finita, quindi non è stato annullato nulla.",
    ],
  ])('reports %s', async (_, answer, text) => {
    vi.mocked(cancelRun).mockResolvedValue({ success: true, data: answer });
    const c = await render(
      <RunActions
        run={live}
        automation={automation}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    await click(control(c, 'Annulla esecuzione'));
    expect(c.querySelector('[role="status"]')?.textContent).toBe(text);
  });

  it('adds the HubSpot sentence to a running run that has a HubSpot step', async () => {
    vi.mocked(cancelRun).mockResolvedValue({
      success: true,
      data: { ...live, cancelRequested: true },
    });
    const c = await render(
      <RunActions
        run={live}
        automation={{
          ...automation,
          definition: { ...automation.definition, steps: [{ type: 'write_hubspot' }] },
        }}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    await click(control(c, 'Annulla esecuzione'));
    expect(c.querySelector('[role="status"]')?.textContent).toMatch(
      /non addebitano nulla\. Se il passo HubSpot ha già scritto dei contatti, ne mostra il numero\.$/,
    );
  });

  it('shows why the API refused', async () => {
    vi.mocked(cancelRun).mockResolvedValue(
      fail(new AppError('AUTOMATION.FORBIDDEN', 'not yours', 403)),
    );
    const c = await render(
      <RunActions
        run={live}
        automation={automation}
        viewer={creator}
        campaignStatus="none"
        instantlyStatus="none"
      />,
    );
    await click(control(c, 'Annulla esecuzione'));
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Solo chi ha creato questa automazione, o un amministratore, può modificarla.',
    );
  });
});

describe('Run now', () => {
  const live = run({ id: '00000000-0000-4000-8000-0000000000c1', status: 'queued', review: null });
  const fresh = run({ id: '00000000-0000-4000-8000-0000000000c2', status: 'queued', review: null });

  it.each([
    ['a queued run it already had', [live], live, "C'è già un'esecuzione in corso."],
    [
      'a running run',
      [],
      { ...live, status: 'running' as const },
      "C'è già un'esecuzione in corso.",
    ],
    ['a new run', [live], fresh, 'Esecuzione in coda. Parte entro un minuto.'],
  ])('reports %s', async (_, before, answer, text) => {
    vi.mocked(getRuns).mockResolvedValue({ success: true, data: { runs: before } });
    vi.mocked(runAutomation).mockResolvedValue({ success: true, data: answer });
    const c = await render(<AutomationControls automation={automation} viewer={creator} />);
    await click(control(c, 'Esegui ora'));
    expect(dialog().textContent).toContain(
      'È la prima esecuzione della versione 2, quindi è da rivedere',
    );
    await click(control(dialog(), 'Esegui ora'));
    expect(c.querySelector('[role="status"]')?.textContent).toContain(text);
  });

  it('gives focus back to its button when Escape closes the dialog', async () => {
    const c = await render(<AutomationControls automation={automation} viewer={creator} />);
    const opener = control(c, 'Esegui ora');
    opener.focus();
    await click(opener);
    await pressEscape(dialog());
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe('pausing and resuming an automation', () => {
  it('pauses it, and shows the missing prerequisite when a resume is refused', async () => {
    vi.mocked(setEnabled).mockResolvedValue(succeed({ ...automation, enabled: false } as never));
    const active = await render(<AutomationControls automation={automation} viewer={creator} />);
    await click(control(active, 'Metti in pausa'));
    expect(vi.mocked(setEnabled).mock.calls).toEqual([[AUTOMATION_ID, false]]);

    vi.mocked(setEnabled).mockResolvedValue(
      fail(new AppError('AUTOMATION.HUBSPOT_OFF', 'connect HubSpot', 422)),
    );
    const paused = await render(
      <AutomationControls automation={{ ...automation, enabled: false }} viewer={creator} />,
    );
    await click(control(paused, 'Riprendi'));
    expect(vi.mocked(setEnabled).mock.calls.at(-1)).toEqual([AUTOMATION_ID, true]);
    expect(paused.querySelector('p[role="alert"]')?.textContent).toBe(
      'Collega prima HubSpot e attiva la scrittura.',
    );
  });
});
