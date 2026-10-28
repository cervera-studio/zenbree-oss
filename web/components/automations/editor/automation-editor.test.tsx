// @vitest-environment happy-dom
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, pressEscape, render } from '@/app/auth/testing';
import type { Definition } from '@/lib/automations/definition';
import { type Choices, type Preview, previewAutomation } from '@/lib/automations/editor-api';
import { httpClient } from '@/lib/http-client';
import { AppError, fail, succeed } from '@/lib/types';
import { AutomationEditor, type SavedAutomation } from './automation-editor';

vi.mock('server-only', () => ({}));
vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/auth-token', () => ({ getToken: vi.fn() }));
vi.mock('@/lib/http-client', () => ({ httpClient: { post: vi.fn() } }));
vi.mock('@/lib/projects/server', () => ({ getProjects: vi.fn() }));
vi.mock('@/lib/campaigns/api', () => ({ getCampaigns: vi.fn() }));
vi.mock('@/lib/automations/editor-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/automations/editor-api')>()),
  previewAutomation: vi.fn(),
  updateAutomation: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

const PEOPLE = '00000000-0000-4000-8000-000000000011';
const CAMPAIGN = '00000000-0000-4000-8000-000000000013';
const choices = {
  people: [{ id: PEOPLE, name: 'Persone Milano' }],
  companies: [],
  campaigns: [{ id: CAMPAIGN, name: 'Lancio', status: 'active' as const }],
  instantly: { state: 'notConnected' as const },
  watchlists: { state: 'ok' as const, items: [] },
};

const preview = (overrides: Partial<Preview> = {}): Preview => ({
  problems: [],
  steps: [{ type: 'add_to_campaign', creditsEach: 0 }],
  people: 3,
  withEmail: 2,
  withContact: 3,
  accounts: null,
  campaign: { id: CAMPAIGN, name: 'Lancio', status: 'draft' },
  instantly: null,
  signals: null,
  peoplePerRun: 25,
  accountsPerRun: 10,
  maxCredits: 0,
  ...overrides,
});

const definition: Definition = {
  listId: PEOPLE,
  steps: [
    { type: 'qualify', brief: 'SaaS B2B', minScore: 50 },
    { type: 'add_to_campaign', campaignId: CAMPAIGN },
  ],
};
const saved: SavedAutomation = {
  id: '00000000-0000-4000-8000-0000000000a1',
  name: 'Lead Milano',
  version: 2,
  enabled: true,
  triggerKind: 'manual',
  cadence: null,
  creditCap: 20,
  signal: null,
  definition,
};

async function settle() {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
}

async function type(input: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = input instanceof HTMLSelectElement ? HTMLSelectElement : HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(
      new Event(proto === HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
    );
  });
}

function select(container: HTMLElement, label: string): HTMLSelectElement {
  const el = [...container.querySelectorAll('label')].find((l) => l.textContent === label);
  const found = el && container.querySelector(`#${CSS.escape(el.htmlFor)}`);
  if (!(found instanceof HTMLSelectElement)) throw new Error(`no select labelled ${label}`);
  return found;
}

function stepSwitch(container: HTMLElement, name: string): HTMLElement {
  const found = [...container.querySelectorAll<HTMLElement>('[role="switch"]')].find(
    (s) => document.getElementById(s.getAttribute('aria-labelledby') ?? '')?.textContent === name,
  );
  if (!found) throw new Error(`no switch for ${name}`);
  return found;
}

const saveButton = (container: HTMLElement) =>
  [...container.querySelectorAll('button')].find((b) =>
    ['Crea automazione', 'Salva', 'Pubblica la versione 3'].includes(b.textContent?.trim() ?? ''),
  );

const peopleShown = (container: HTMLElement) => container.querySelector('dd')?.textContent;

type PreviewResult = Awaited<ReturnType<typeof previewAutomation>>;
function deferred() {
  let resolve: (value: PreviewResult) => void = () => {};
  const promise = new Promise<PreviewResult>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

async function answer(pending: ReturnType<typeof deferred>, people: number) {
  await act(async () => pending.resolve(succeed(preview({ people }))));
}

async function newHubspotPlan() {
  const c = await render(<AutomationEditor choices={choices} viewerIsCreator />);
  await type(field(c, 'Nome'), 'Lead Milano');
  await type(select(c, 'Lista di persone'), PEOPLE);
  await click(stepSwitch(c, 'Scrivi su HubSpot'));
  return c;
}

describe('the automation editor', () => {
  it('previews the form and holds the create button while the preview names problems', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(
      succeed(
        preview({
          problems: [
            { code: 'AUTOMATION.LIST_NOT_FOUND', message: 'pick a person list of this project' },
            { code: 'AUTOMATION.HUBSPOT_OFF', message: 'connect HubSpot' },
          ],
          people: null,
        }),
      ),
    );
    const c = await render(<AutomationEditor choices={choices} viewerIsCreator />);
    await type(field(c, 'Nome'), 'Lead Milano');
    await type(select(c, 'Lista di persone'), PEOPLE);
    await click(stepSwitch(c, 'Scrivi su HubSpot'));
    await click(control(c, 'Anteprima'));
    await settle();

    expect(previewAutomation).toHaveBeenCalledWith(
      { definition: { listId: PEOPLE, steps: [{ type: 'write_hubspot' }] }, creditCap: 10 },
      { kind: 'manual' },
      undefined,
    );
    const alert = c.querySelector('section[role="alert"]');
    expect([...(alert?.querySelectorAll('li') ?? [])].map((li) => li.textContent)).toEqual([
      'Scegli una lista di persone di questo cliente.',
      'Collega prima HubSpot e attiva la scrittura.',
    ]);
    expect(saveButton(c)?.textContent).toBe('Crea automazione');
    expect(saveButton(c)?.disabled).toBe(true);

    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    await click(control(c, 'Anteprima'));
    await settle();
    expect(c.textContent).toContain("C'è tutto quello che serve.");
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('previews a webhook draft again, as one person a run, and says where the secret is made', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    const c = await newHubspotPlan();
    await settle();
    expect(c.textContent).toContain('25 persone');

    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview({ peoplePerRun: 1 })));
    const webhook = c.querySelector<HTMLInputElement>('input[type="radio"][value="webhook"]');
    if (!webhook) throw new Error('no webhook trigger');
    await click(webhook);
    await settle();

    expect(vi.mocked(previewAutomation).mock.lastCall?.[1]).toEqual({ kind: 'webhook' });
    expect(c.textContent).toContain('1 persona');
    expect(c.textContent).toContain("la pagina dell'automazione mostra l'URL e il segreto");
  });

  it('warns that a running campaign sends to the people once a review run is approved', async () => {
    const warning =
      "Questa campagna è attiva. Dopo che approvi un'esecuzione di revisione, le esecuzioni successive aggiungono le persone come attive e la campagna invia loro i messaggi.";
    const active = preview({ campaign: { id: CAMPAIGN, name: 'Lancio', status: 'active' } });
    const c = await render(
      <AutomationEditor choices={choices} viewerIsCreator saved={saved} initialPreview={active} />,
    );
    expect(c.textContent).toContain(warning);
    const draft = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    expect(draft.textContent).not.toContain(warning);
  });

  it('publishes a new version for a step edit and only saves a rename', async () => {
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    expect(saveButton(c)?.textContent).toBe('Salva');
    await type(field(c, 'Punteggio minimo'), '60');
    expect(saveButton(c)?.textContent).toBe('Pubblica la versione 3');
    await type(field(c, 'Punteggio minimo'), '50');
    await type(field(c, 'Nome'), 'Lead Roma');
    expect(saveButton(c)?.textContent).toBe('Salva');
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('offers Preview again with the error when the preview request fails', async () => {
    vi.mocked(previewAutomation).mockRejectedValue(new Error('fetch failed'));
    const c = await newHubspotPlan();
    await click(control(c, 'Anteprima'));
    await settle();

    expect(c.querySelector('p[role="alert"]')?.textContent).toBe(
      "Non è stato possibile caricare l'anteprima. Riprova.",
    );
    expect(c.textContent).not.toContain('Controllo in corso…');
    expect(control(c, 'Anteprima').hasAttribute('disabled')).toBe(false);
    expect(saveButton(c)?.disabled).toBe(true);

    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    await click(control(c, 'Anteprima'));
    expect(c.textContent).toContain('Controllo in corso…');
    expect(c.querySelector('p[role="alert"]')).toBe(null);
    await settle();
    expect(c.textContent).toContain("C'è tutto quello che serve.");
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('shows no earlier answer beside a failed preview of an edit', async () => {
    vi.mocked(previewAutomation).mockRejectedValue(new Error('fetch failed'));
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    expect(c.textContent).toContain("C'è tutto quello che serve.");
    await type(field(c, 'Punteggio minimo'), '60');
    await settle();

    expect(c.querySelector('p[role="alert"]')?.textContent).toBe(
      "Non è stato possibile caricare l'anteprima. Riprova.",
    );
    expect(c.textContent).not.toContain("C'è tutto quello che serve.");
  });

  it('holds the publish button from an edit until the preview of that edit answers', async () => {
    const pending = deferred();
    vi.mocked(previewAutomation).mockReturnValue(pending.promise);
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    await type(field(c, 'Punteggio minimo'), '60');
    expect(saveButton(c)?.textContent).toBe('Pubblica la versione 3');
    expect(saveButton(c)?.disabled).toBe(true);
    await settle();
    expect(saveButton(c)?.disabled).toBe(true);

    await answer(pending, 4);
    expect(peopleShown(c)).toBe('4');
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('keeps checking while a newer edit waits, and shows only the newest answer', async () => {
    const first = deferred();
    const second = deferred();
    vi.mocked(previewAutomation)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    await type(field(c, 'Punteggio minimo'), '60');
    await settle();
    await type(field(c, 'Punteggio minimo'), '70');

    await answer(first, 4);
    expect(c.textContent).toContain('Controllo in corso…');
    expect(saveButton(c)?.disabled).toBe(true);

    await settle();
    await answer(second, 7);
    expect(c.textContent).not.toContain('Controllo in corso…');
    expect(peopleShown(c)).toBe('7');
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('never lets an older answer replace a newer one', async () => {
    const first = deferred();
    const second = deferred();
    vi.mocked(previewAutomation)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    await type(field(c, 'Punteggio minimo'), '60');
    await settle();
    await type(field(c, 'Punteggio minimo'), '70');
    await settle();

    await answer(second, 7);
    await answer(first, 4);
    expect(peopleShown(c)).toBe('7');
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('creates the automation paused', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    vi.mocked(httpClient.post).mockResolvedValue(
      fail(new AppError('AUTOMATION.HUBSPOT_OFF', 'connect HubSpot', 422)),
    );
    const c = await newHubspotPlan();
    await click(control(c, 'Anteprima'));
    await settle();
    await click(control(c, 'Crea automazione'));

    expect(vi.mocked(httpClient.post).mock.calls.map(([path, body]) => [path, body])).toEqual([
      [
        '/v1/automations',
        {
          definition: { listId: PEOPLE, steps: [{ type: 'write_hubspot' }] },
          creditCap: 10,
          name: 'Lead Milano',
          trigger: { kind: 'manual' },
          enabled: false,
        },
      ],
    ]);
  });

  it('says how many companies a run looks at, as the preview answers', async () => {
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview({ accounts: 40, accountsPerRun: 7 })}
      />,
    );
    expect(c.textContent).toContain(
      "Ogni esecuzione guarda fino a 7 aziende, le successive nell'ordine della lista.",
    );
  });

  it('counts a name as saved when only spaces around it differ', async () => {
    const c = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={preview()}
      />,
    );
    await type(field(c, 'Nome'), 'Lead Milano ');
    expect(saveButton(c)?.disabled).toBe(true);
    await type(field(c, 'Nome'), 'Lead Roma ');
    expect(saveButton(c)?.disabled).toBe(false);
  });

  it('lets a paused automation save with problems, which its resume checks again', async () => {
    const problems = preview({
      problems: [{ code: 'AUTOMATION.LIST_NOT_FOUND', message: 'pick a person list' }],
    });
    const note =
      'È in pausa, quindi puoi salvarla. Quando la riprendi, Zenbree ricontrolla questi punti e la lascia in pausa se ne resta uno.';
    const paused = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={{ ...saved, enabled: false }}
        initialPreview={problems}
      />,
    );
    await type(field(paused, 'Nome'), 'Lead Roma');
    expect(paused.textContent).toContain(note);
    expect(saveButton(paused)?.disabled).toBe(false);

    const enabled = await render(
      <AutomationEditor
        choices={choices}
        viewerIsCreator
        saved={saved}
        initialPreview={problems}
      />,
    );
    await type(field(enabled, 'Nome'), 'Lead Roma');
    expect(enabled.textContent).toContain('Correggi questi punti prima di salvare');
    expect(saveButton(enabled)?.disabled).toBe(true);
  });

  it('sends a client with no people list to the chat that makes one', async () => {
    const c = await render(
      <AutomationEditor choices={{ ...choices, people: [] }} viewerIsCreator />,
    );
    const list = select(c, 'Lista di persone');
    const hint = document.getElementById(list.getAttribute('aria-describedby') ?? '');
    expect(hint?.textContent).toBe(
      'Non hai ancora liste di persone. Apri una chat e descrivi chi vuoi raggiungere: la lista nasce quando parte la ricerca.',
    );
    const link = hint?.querySelector('a');
    expect(link?.textContent).toBe('Apri una chat');
    expect(link?.getAttribute('href')).toBe('/');
  });

  it('keeps the plain list hint, with no link, for a client that has people lists', async () => {
    const c = await render(<AutomationEditor choices={choices} viewerIsCreator />);
    const list = select(c, 'Lista di persone');
    const hint = document.getElementById(list.getAttribute('aria-describedby') ?? '');
    expect(hint?.textContent).toBe(
      'Le persone su cui lavora. Le persone che trova vengono aggiunte a questa lista.',
    );
    expect(hint?.querySelector('a')).toBeNull();
  });

  it('tells a page that loaded with no queued run that a new version cancels one', async () => {
    await openPublish();
    expect(document.querySelector('[role="alertdialog"] ul')?.textContent).toContain(
      "Un'esecuzione in coda, se c'è, viene annullata.",
    );
  });

  it('gives focus back to the publish button when Escape closes its dialog', async () => {
    const c = await openPublish();
    vi.useRealTimers();
    const button = saveButton(c);
    if (!button) throw new Error('no publish button');
    const dialog = document.querySelector<HTMLElement>('[role="alertdialog"]');
    if (!dialog) throw new Error('no dialog');
    await pressEscape(dialog);
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
    expect(document.activeElement).toBe(button);
  });
});

async function openPublish() {
  vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
  const c = await render(
    <AutomationEditor choices={choices} viewerIsCreator saved={saved} initialPreview={preview()} />,
  );
  await type(field(c, 'Punteggio minimo'), '60');
  await settle();
  const button = saveButton(c);
  if (!button || button.disabled) throw new Error('publish button is held');
  button.focus();
  await click(button);
  return c;
}

describe('the Instantly step', () => {
  const RUNNING = '0a1b2c3d-0000-4000-8000-000000000001';
  const PAUSED = '0a1b2c3d-0000-4000-8000-000000000002';
  const connected = {
    ...choices,
    instantly: {
      state: 'ready' as const,
      campaigns: [
        { id: RUNNING, name: 'Verify running', status: 1, maySend: true },
        { id: PAUSED, name: 'Verify paused', status: 2, maySend: false },
      ],
    },
  };
  const sends =
    'Questa campagna è attiva: Instantly può iniziare a inviare email appena le persone vengono aggiunte. Per ogni nuova versione, nessuno viene aggiunto finché non approvi la prima esecuzione.';
  const instantlyPlan: Definition = {
    listId: PEOPLE,
    steps: [
      { type: 'qualify', brief: 'SaaS B2B', minScore: 50 },
      { type: 'add_to_instantly', campaignId: RUNNING },
    ],
  };
  const reading = (instantly: Preview['instantly']) =>
    preview({
      steps: [
        { type: 'qualify', creditsEach: 1 },
        { type: 'add_to_instantly', creditsEach: 0 },
      ],
      campaign: null,
      instantly,
    });
  const running = {
    connected: true,
    campaign: {
      id: RUNNING,
      name: 'Verify running',
      status: 1,
      maySend: true,
      url: 'https://app.instantly.ai/app/campaign/x',
    },
    unreadable: null,
  };

  it('picks a campaign of the workspace by name and status, and warns for a running one', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    const c = await render(<AutomationEditor choices={connected} viewerIsCreator />);
    await type(field(c, 'Nome'), 'Lead Milano');
    await type(select(c, 'Lista di persone'), PEOPLE);
    await click(stepSwitch(c, 'Aggiungi a Instantly'));
    expect(c.textContent).toContain('Scegli una campagna.');

    const picker = select(c, 'Campagna Instantly');
    expect([...picker.options].map((o) => o.textContent)).toEqual([
      'Scegli',
      'Verify running (In corso)',
      'Verify paused (In pausa)',
    ]);
    await type(picker, PAUSED);
    expect(c.textContent).not.toContain(sends);
    await type(picker, RUNNING);
    expect(c.textContent).toContain(sends);
    await settle();
    expect(vi.mocked(previewAutomation).mock.lastCall?.[0]).toEqual({
      definition: { listId: PEOPLE, steps: [{ type: 'add_to_instantly', campaignId: RUNNING }] },
      creditCap: 10,
    });
  });

  it.each([
    [
      { state: 'notConnected' } as const,
      'Collega Instantly a questo cliente per scegliere una campagna.',
    ],
    [
      { state: 'unreadable', code: 'INSTANTLY.DISABLED' } as const,
      'Instantly è disattivato per ora. Riprova più tardi.',
    ],
  ])('offers Integrations, not a picker, when Instantly is %j', async (instantly, text) => {
    const c = await render(
      <AutomationEditor
        choices={{ ...choices, instantly }}
        viewerIsCreator
        saved={{ ...saved, definition: instantlyPlan }}
        initialPreview={reading(running)}
      />,
    );
    expect(c.textContent).toContain(text);
    expect(c.textContent).toContain(
      "La campagna scelta resta salvata finché non ne scegli un'altra.",
    );
    expect(c.querySelector('a[href="/settings/integrations"]')?.textContent).toBe(
      'Apri Integrazioni',
    );
    expect(() => select(c, 'Campagna Instantly')).toThrow();
    expect(saveButton(c)?.textContent).toBe('Salva');
  });

  it('asks for one campaign system, and previews nothing while both are on', async () => {
    const c = await render(<AutomationEditor choices={connected} viewerIsCreator />);
    await type(field(c, 'Nome'), 'Lead Milano');
    await type(select(c, 'Lista di persone'), PEOPLE);
    await click(stepSwitch(c, 'Aggiungi alla campagna'));
    await type(select(c, 'Campagna'), CAMPAIGN);
    await click(stepSwitch(c, 'Aggiungi a Instantly'));
    await type(select(c, 'Campagna Instantly'), PAUSED);
    await settle();
    expect(c.textContent).toContain(
      "Scegli Aggiungi alla campagna oppure Aggiungi a Instantly: un'automazione può usarne solo uno.",
    );
    expect(previewAutomation).not.toHaveBeenCalled();
    expect(saveButton(c)?.disabled).toBe(true);
  });

  it('previews the campaign Instantly has now, and warns that a running one sends', async () => {
    const c = await render(
      <AutomationEditor
        choices={connected}
        viewerIsCreator
        saved={{ ...saved, definition: instantlyPlan }}
        initialPreview={reading(running)}
      />,
    );
    expect(c.textContent).toContain('Aggiungi a Instantly · Verify running (In corso)');
    expect(c.textContent).toContain(
      "Questa campagna Instantly è attiva. Dopo l'approvazione della prima esecuzione, le persone vengono aggiunte subito e Instantly può iniziare a inviare email. Le esecuzioni successive le aggiungono senza revisione.",
    );
  });

  it.each([
    [
      'INTEGRATIONS.CRYPTO_FAILED',
      "Non riusciamo a leggere le credenziali salvate. Ricollega l'account.",
    ],
    ['INTEGRATIONS.NOT_CONFIGURED', 'Questa integrazione non è disponibile al momento.'],
    [
      'INSTANTLY.INVALID_KEY',
      "Instantly ha rifiutato la chiave o il piano dell'account. Ricollegalo da Integrazioni.",
    ],
    [
      'INSTANTLY.PLAN_REQUIRED',
      "Instantly ha rifiutato la chiave o il piano dell'account. Ricollegalo da Integrazioni.",
    ],
    [
      'INSTANTLY.REJECTED',
      "Instantly ha rifiutato la chiave o il piano dell'account. Ricollegalo da Integrazioni.",
    ],
    ['INSTANTLY.DISABLED', 'Un fornitore di dati è spento o non collegato.'],
  ])(
    'gives no all-clear for %s, which fails every run, and says it as the run will',
    async (code, text) => {
      const c = await render(
        <AutomationEditor
          choices={connected}
          viewerIsCreator
          saved={{ ...saved, definition: instantlyPlan }}
          initialPreview={reading({ connected: true, campaign: null, unreadable: code })}
        />,
      );
      expect(c.textContent).toContain(text);
      expect(c.textContent).not.toContain('campi associati');
      expect(c.textContent).not.toContain('Al momento non riusciamo a leggere la campagna');
      expect(c.textContent).not.toContain("C'è tutto quello che serve.");
    },
  );

  it('notes an Instantly that did not answer, and lists a campaign it does not have', async () => {
    const quiet = await render(
      <AutomationEditor
        choices={connected}
        viewerIsCreator
        saved={{ ...saved, definition: instantlyPlan }}
        initialPreview={reading({
          connected: true,
          campaign: null,
          unreadable: 'INSTANTLY.UNKNOWN_OUTCOME',
        })}
      />,
    );
    expect(quiet.textContent).toContain(
      "Al momento non riusciamo a leggere la campagna su Instantly. L'esecuzione la controllerà di nuovo quando arriva a questo passo.",
    );
    expect(quiet.querySelector('section[role="alert"]')).toBeNull();

    expect(quiet.textContent).toContain("C'è tutto quello che serve.");

    const gone = reading({ connected: true, campaign: null, unreadable: null });
    gone.problems = [
      { code: 'AUTOMATION.INSTANTLY_CAMPAIGN_NOT_FOUND', message: 'pick a campaign' },
    ];
    const missing = await render(
      <AutomationEditor
        choices={connected}
        viewerIsCreator
        saved={{ ...saved, definition: instantlyPlan }}
        initialPreview={gone}
      />,
    );
    expect(missing.querySelector('section[role="alert"]')?.textContent).toContain(
      "Scegli una campagna dell'account Instantly collegato a questo cliente.",
    );
  });

  it('says what a new version does to the Instantly hold before it publishes', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(reading(running)));
    const c = await render(
      <AutomationEditor
        choices={connected}
        viewerIsCreator
        saved={{ ...saved, definition: instantlyPlan }}
        initialPreview={reading(running)}
      />,
    );
    await type(field(c, 'Punteggio minimo'), '60');
    await settle();
    const button = saveButton(c);
    if (!button || button.disabled) throw new Error('publish button is held');
    await click(button);
    const lines = [...document.querySelectorAll('[role="alertdialog"] li')].map(
      (li) => li.textContent,
    );
    expect(lines).toContain(
      'Finché non approvi la prima esecuzione, nessuno viene aggiunto a Instantly.',
    );
    expect(lines).toContain(
      "Le persone in attesa per Instantly da un'esecuzione non approvata non vengono aggiunte. Le riprenderà la prossima esecuzione.",
    );
    expect(lines).toContain(
      "La campagna Instantly è attiva. Dopo l'approvazione, le esecuzioni successive aggiungono le persone e Instantly può iniziare a inviare email.",
    );
    expect(lines.some((l) => l?.includes('bozze'))).toBe(false);
  });
});

describe('the signal trigger', () => {
  const WATCHLIST = '00000000-0000-4000-8000-000000000014';
  const PAUSED = '00000000-0000-4000-8000-000000000015';
  const watching = {
    ...choices,
    watchlists: {
      state: 'ok' as const,
      items: [
        { id: WATCHLIST, name: 'Assunzioni', enabled: true, listName: 'Aziende in crescita' },
        { id: PAUSED, name: 'Ferma', enabled: false, listName: 'Aziende ferme' },
      ],
    },
  };
  const signals = {
    accounts: 2,
    recent: [
      {
        accountId: 'r1',
        name: 'Hiring Srl',
        title: 'Senior SDR',
        score: 149.6,
        seenAt: '2026-10-20T09:00:00Z',
      },
      { accountId: 'r2', name: null, title: null, score: 90, seenAt: '2026-10-19T09:00:00Z' },
    ],
  };

  async function signalDraft(choices: Choices = watching) {
    const c = await render(<AutomationEditor choices={choices} viewerIsCreator />);
    await type(field(c, 'Nome'), 'Segnali');
    await type(select(c, 'Lista di persone'), PEOPLE);
    await click(stepSwitch(c, 'Scrivi su HubSpot'));
    const signal = c.querySelector<HTMLInputElement>('input[type="radio"][value="signal"]');
    if (!signal) throw new Error('no signal trigger');
    await click(signal);
    return c;
  }

  it('creates a signal automation from a watchlist and a minimum, after a preview of recent signals', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(
      succeed(preview({ signals, peoplePerRun: 25, accountsPerRun: 1 })),
    );
    vi.mocked(httpClient.post).mockResolvedValue(
      fail(new AppError('AUTOMATION.HUBSPOT_OFF', 'connect HubSpot', 422)),
    );
    const c = await signalDraft();
    await type(select(c, 'Watchlist'), WATCHLIST);
    await type(field(c, 'Punteggio minimo dei segnali'), '80');
    await settle();

    const trigger = { kind: 'signal', watchlistId: WATCHLIST, minScore: 80 };
    expect(vi.mocked(previewAutomation).mock.lastCall?.[1]).toEqual(trigger);
    expect(c.textContent).toContain(
      '2 aziende della watchlist hanno segnali degli ultimi 30 giorni che raggiungono il punteggio minimo.',
    );
    expect(c.textContent).toContain('Hiring SrlPunteggio 150');
    expect(c.textContent).toContain('Senior SDR · ultimo segnale il');
    expect(c.textContent).toContain('Azienda senza nomePunteggio 90');
    expect(c.textContent).toContain(
      "Avviano esecuzioni solo i segnali che arrivano quando l'automazione è attiva.",
    );
    expect(c.textContent).toContain('25 persone');

    await click(control(c, 'Crea automazione'));
    expect(vi.mocked(httpClient.post).mock.calls.map(([path, body]) => [path, body])).toEqual([
      [
        '/v1/automations',
        {
          definition: { listId: PEOPLE, steps: [{ type: 'write_hubspot' }] },
          creditCap: 10,
          name: 'Segnali',
          trigger,
          enabled: false,
        },
      ],
    ]);
  });

  it('says that a signal run looks only at its own company', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(
      succeed(preview({ accounts: 40, accountsPerRun: 1 })),
    );
    const c = await signalDraft();
    await type(select(c, 'Watchlist'), WATCHLIST);
    await settle();
    expect(c.textContent).toContain(
      "Ogni esecuzione guarda solo l'azienda i cui segnali l'hanno avviata.",
    );
    expect(c.textContent).not.toContain('Ogni esecuzione guarda fino a');
  });

  it('says in one sentence that the client has no watchlist', async () => {
    const c = await signalDraft({ ...choices, watchlists: { state: 'ok', items: [] } });
    expect(c.textContent).toContain(
      "Questo cliente non ha ancora una watchlist, quindi nessun segnale può avviare un'esecuzione.",
    );
    expect(() => select(c, 'Watchlist')).toThrow();
  });

  it('says in one sentence that the watchlists could not be loaded, and keeps the rest of the editor', async () => {
    const c = await signalDraft({ ...choices, watchlists: { state: 'unreadable' } });
    expect(c.textContent).toContain(
      'Impossibile caricare le watchlist. Ricarica la pagina per riprovare.',
    );
    expect(() => select(c, 'Watchlist')).toThrow();
    expect(select(c, 'Lista di persone').value).toBe(PEOPLE);
  });

  it('names the company list of the picked watchlist', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    const c = await signalDraft();
    const generic =
      'Le aziende di cui segue i segnali. Con Trova persone, cerca nella lista di aziende di questa watchlist.';
    expect(c.textContent).toContain(generic);
    await type(select(c, 'Watchlist'), WATCHLIST);
    expect(c.textContent).toContain(
      'Con Trova persone, cerca in Aziende in crescita, la lista di aziende di questa watchlist.',
    );
    expect(c.textContent).not.toContain(generic);
  });

  it('marks a paused watchlist and shows the problem the preview names', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(
      succeed(
        preview({
          problems: [{ code: 'AUTOMATION.WATCHLIST_PAUSED', message: 'resume the watchlist' }],
        }),
      ),
    );
    const c = await signalDraft();
    const picker = select(c, 'Watchlist');
    expect([...picker.options].map((o) => o.textContent)).toEqual([
      'Scegli',
      'Assunzioni',
      'Ferma (in pausa)',
    ]);
    await type(picker, PAUSED);
    await settle();
    const alert = c.querySelector('section[role="alert"]');
    expect(alert?.querySelector('li')?.textContent).toBe(
      'Riattiva prima la watchlist: in pausa non trova segnali.',
    );
    expect(saveButton(c)?.disabled).toBe(true);
  });

  it('takes a minimum below 1, as the API does', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    const c = await signalDraft();
    await type(select(c, 'Watchlist'), WATCHLIST);
    const minimum = field(c, 'Punteggio minimo dei segnali');
    await type(minimum, '0.5');
    await settle();
    expect(minimum.validity.valid).toBe(true);
    expect(minimum.inputMode).toBe('decimal');
    expect(vi.mocked(previewAutomation).mock.lastCall?.[1]).toEqual({
      kind: 'signal',
      watchlistId: WATCHLIST,
      minScore: 0.5,
    });
    expect(c.textContent).not.toContain('Imposta il punteggio minimo dei segnali sopra 0');
  });

  it('holds the create button while the minimum is not above 0', async () => {
    vi.mocked(previewAutomation).mockResolvedValue(succeed(preview()));
    const c = await signalDraft();
    await type(select(c, 'Watchlist'), WATCHLIST);
    await type(field(c, 'Punteggio minimo dei segnali'), '0');
    await settle();
    expect(c.textContent).toContain(
      'Imposta il punteggio minimo dei segnali sopra 0 e fino a 10.000.',
    );
    expect(saveButton(c)?.disabled).toBe(true);
  });
});
