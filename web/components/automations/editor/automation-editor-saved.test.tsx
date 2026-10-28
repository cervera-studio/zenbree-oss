// @vitest-environment happy-dom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, field, TestIntl } from '@/app/auth/testing';
import type { Definition, DefinitionStep } from '@/lib/automations/definition';
import { type Preview, previewAutomation } from '@/lib/automations/editor-api';
import { succeed } from '@/lib/types';
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
}));

const PEOPLE = '00000000-0000-4000-8000-000000000011';
const COMPANIES = '00000000-0000-4000-8000-000000000012';
const CAMPAIGN = '00000000-0000-4000-8000-000000000013';
const choices = {
  people: [{ id: PEOPLE, name: 'Persone Milano' }],
  companies: [{ id: COMPANIES, name: 'Aziende Milano' }],
  campaigns: [{ id: CAMPAIGN, name: 'Lancio', status: 'active' as const }],
  instantly: { state: 'notConnected' as const },
  watchlists: { state: 'ok' as const, items: [] },
};

const preview: Preview = {
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
};

const stored = (version: number, definition: Definition): SavedAutomation => ({
  id: '00000000-0000-4000-8000-0000000000a1',
  name: 'Lead Milano',
  version,
  enabled: true,
  triggerKind: 'manual',
  cadence: null,
  creditCap: 20,
  signal: null,
  definition,
});

const findPeople = (titles: string[]): DefinitionStep => ({
  type: 'find_people',
  accountsListId: COMPANIES,
  titles,
  seniorities: [],
  perAccount: 3,
});
const qualify = { type: 'qualify', brief: 'SaaS B2B', minScore: 50 } as const;
const addToCampaign = { type: 'add_to_campaign', campaignId: CAMPAIGN } as const;

let root: Root | null = null;
let container: HTMLElement;

async function mount(ui: ReactNode) {
  if (!root) {
    container = document.createElement('div');
    document.body.replaceChildren(container);
    root = createRoot(container);
  }
  const current = root;
  await act(async () => current.render(<TestIntl>{ui}</TestIntl>));
  return container;
}

async function unmount() {
  const current = root;
  root = null;
  await act(async () => current?.unmount());
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  vi.mocked(previewAutomation).mockResolvedValue(succeed(preview));
});
afterEach(async () => {
  await unmount();
  vi.useRealTimers();
});

const editor = (saved?: SavedAutomation) => (
  <AutomationEditor choices={choices} viewerIsCreator saved={saved} initialPreview={preview} />
);

async function settle() {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
}

function stepSwitch(c: HTMLElement, name: string): HTMLElement {
  const found = [...c.querySelectorAll<HTMLElement>('[role="switch"]')].find(
    (s) => document.getElementById(s.getAttribute('aria-labelledby') ?? '')?.textContent === name,
  );
  if (!found) throw new Error(`no switch for ${name}`);
  return found;
}

async function typeTitles(c: HTMLElement, value: string) {
  const label = [...c.querySelectorAll('label')].find((l) => l.textContent === 'Ruoli');
  const area = label && c.querySelector(`#${CSS.escape(label.htmlFor)}`);
  if (!(area instanceof HTMLTextAreaElement)) throw new Error('no titles field');
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const button = (c: HTMLElement) =>
  [...c.querySelectorAll('button')].find((b) =>
    ['Salva', 'Pubblica la versione 3', 'Crea automazione'].includes(b.textContent?.trim() ?? ''),
  );
const saveState = (c: HTMLElement) => ({
  label: button(c)?.textContent?.trim(),
  disabled: button(c)?.disabled,
});

/** `true` when the leave prompt holds a reload or a closed tab. */
const leaveHeld = () => !window.dispatchEvent(new Event('beforeunload', { cancelable: true }));

describe('the editor after a save', () => {
  it('is clean once the page passes the version that a step turned off made', async () => {
    const c = await mount(editor(stored(2, { listId: PEOPLE, steps: [qualify, addToCampaign] })));
    await click(stepSwitch(c, "Qualifica con l'AI"));
    await settle();
    expect(saveState(c)).toEqual({ label: 'Pubblica la versione 3', disabled: false });
    expect(leaveHeld()).toBe(true);

    await mount(editor(stored(3, { listId: PEOPLE, steps: [addToCampaign] })));
    expect(saveState(c)).toEqual({ label: 'Salva', disabled: true });
    expect(leaveHeld()).toBe(false);
  });

  it('is clean once the page passes the titles that an edit stored', async () => {
    const c = await mount(
      editor(stored(2, { listId: PEOPLE, steps: [findPeople(['CEO', 'CTO'])] })),
    );
    await typeTitles(c, 'CEO\nCFO');
    await settle();
    expect(saveState(c)).toEqual({ label: 'Pubblica la versione 3', disabled: false });
    expect(leaveHeld()).toBe(true);

    await mount(editor(stored(3, { listId: PEOPLE, steps: [findPeople(['CEO', 'CFO'])] })));
    expect(saveState(c)).toEqual({ label: 'Salva', disabled: true });
    expect(leaveHeld()).toBe(false);
  });

  it('counts titles as saved when only blank lines differ', async () => {
    const c = await mount(
      editor(stored(2, { listId: PEOPLE, steps: [findPeople(['CEO', 'CTO'])] })),
    );
    await typeTitles(c, 'CEO\nCFO');
    expect(leaveHeld()).toBe(true);

    await typeTitles(c, 'CEO\n   \n\nCTO');
    await settle();
    expect(saveState(c)).toEqual({ label: 'Salva', disabled: true });
    expect(leaveHeld()).toBe(false);
  });

  it('counts a cleared field as unsaved', async () => {
    const c = await mount(
      editor(stored(2, { listId: PEOPLE, steps: [findPeople(['CEO', 'CTO'])] })),
    );
    expect(leaveHeld()).toBe(false);
    await typeTitles(c, '');
    expect(leaveHeld()).toBe(true);
  });
});

describe('the preview timer', () => {
  async function newHubspotPlan() {
    const c = await mount(editor());
    await act(async () => {
      const name = field(c, 'Nome');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(name, 'Lead');
      name.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const label = [...c.querySelectorAll('label')].find(
      (l) => l.textContent === 'Lista di persone',
    );
    const list = label && c.querySelector(`#${CSS.escape(label.htmlFor)}`);
    if (!(list instanceof HTMLSelectElement)) throw new Error('no person list field');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(
        list,
        PEOPLE,
      );
      list.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await click(stepSwitch(c, 'Scrivi su HubSpot'));
  }
  const hubspotPlan = {
    definition: { listId: PEOPLE, steps: [{ type: 'write_hubspot' }] },
    creditCap: 10,
  };

  it('sends no preview once the editor is gone', async () => {
    await newHubspotPlan();
    await settle();
    expect(vi.mocked(previewAutomation).mock.calls).toEqual([
      [hubspotPlan, { kind: 'manual' }, undefined],
    ]);

    await unmount();
    vi.mocked(previewAutomation).mockClear();
    await newHubspotPlan();
    await unmount();
    await settle();
    expect(vi.mocked(previewAutomation).mock.calls).toEqual([]);
  });
});

describe('the editor of a signal automation', () => {
  const WATCHLIST = '00000000-0000-4000-8000-000000000014';
  const radio = (c: HTMLElement, value: string) =>
    c.querySelector<HTMLInputElement>(`input[type="radio"][value="${value}"]`);
  const signalAutomation = (watchlistId: string | null): SavedAutomation => ({
    ...stored(1, { listId: PEOPLE, steps: [addToCampaign] }),
    triggerKind: 'signal',
    signal: {
      watchlistId,
      watchlistName: watchlistId && 'Assunzioni',
      minScore: 80,
      since: '2026-10-21T08:00:00Z',
    },
  });
  const watching = {
    ...choices,
    watchlists: {
      state: 'ok' as const,
      items: [
        { id: WATCHLIST, name: 'Assunzioni', enabled: true, listName: 'Aziende in crescita' },
        {
          id: '00000000-0000-4000-8000-000000000015',
          name: 'Altra',
          enabled: true,
          listName: 'Aziende in crescita',
        },
      ],
    },
  };
  const signalEditor = (saved: SavedAutomation) => (
    <AutomationEditor choices={watching} viewerIsCreator saved={saved} initialPreview={preview} />
  );
  const pick = (c: HTMLElement, label: string) => {
    const el = [...c.querySelectorAll('label')].find((l) => l.textContent === label);
    const found = el && c.querySelector(`#${CSS.escape(el.htmlFor)}`);
    if (!(found instanceof HTMLSelectElement)) throw new Error(`no select labelled ${label}`);
    return found;
  };

  it('keeps the picked watchlist and minimum after a switch to another trigger and back', async () => {
    const c = await mount(signalEditor(signalAutomation(WATCHLIST)));
    expect(radio(c, 'signal')?.checked).toBe(true);
    expect(pick(c, 'Watchlist').value).toBe(WATCHLIST);
    expect(field(c, 'Punteggio minimo dei segnali').value).toBe('80');
    const manual = radio(c, 'manual');
    if (!manual) throw new Error('no manual trigger');
    await click(manual);
    expect(c.textContent).not.toContain('Punteggio minimo dei segnali');
    const signal = radio(c, 'signal');
    if (!signal) throw new Error('the signal trigger is gone');
    await click(signal);
    expect(pick(c, 'Watchlist').value).toBe(WATCHLIST);
    expect(saveState(c).disabled).toBe(true);
  });

  it('warns before a save that leaves the signals', async () => {
    const c = await mount(signalEditor(signalAutomation(WATCHLIST)));
    const manual = radio(c, 'manual');
    if (!manual) throw new Error('no manual trigger');
    await click(manual);
    await settle();
    const save = button(c);
    if (!save || save.disabled) throw new Error('the save button is held');
    await click(save);
    const lines = [...document.querySelectorAll('[role="alertdialog"] li')].map(
      (li) => li.textContent,
    );
    expect(lines).toEqual([
      "L'automazione non parte più dai segnali della watchlist: la watchlist e il punteggio minimo vengono rimossi.",
      "Un'esecuzione in coda, se c'è, viene annullata.",
    ]);
  });

  it('says that another watchlist counts only the signals after the save', async () => {
    const c = await mount(signalEditor(signalAutomation(WATCHLIST)));
    const other = pick(c, 'Watchlist');
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(other, '00000000-0000-4000-8000-000000000015');
      other.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();
    const save = button(c);
    if (!save || save.disabled) throw new Error('the save button is held');
    await click(save);
    const lines = [...document.querySelectorAll('[role="alertdialog"] li')].map(
      (li) => li.textContent,
    );
    expect(lines).toEqual([
      "Avviano esecuzioni solo i segnali che arrivano dopo questo salvataggio, e nessuno mentre l'automazione è in pausa.",
      "Un'esecuzione in coda, se c'è, viene annullata.",
    ]);
  });

  it('says that the saved watchlist was deleted and previews the trigger with none', async () => {
    const c = await mount(signalEditor(signalAutomation(null)));
    expect(pick(c, 'Watchlist').value).toBe('');
    expect(c.textContent).toContain(
      "La watchlist di questa automazione è stata eliminata. Scegline un'altra.",
    );
    const score = field(c, 'Punteggio minimo dei segnali');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(score, '90');
      score.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await settle();
    expect(vi.mocked(previewAutomation).mock.lastCall?.[1]).toEqual({
      kind: 'signal',
      watchlistId: null,
      minScore: 90,
    });
  });

  it('offers the signal trigger to every automation', async () => {
    const c = await mount(editor(stored(1, { listId: PEOPLE, steps: [addToCampaign] })));
    expect(radio(c, 'manual')?.checked).toBe(true);
    expect(radio(c, 'signal')?.checked).toBe(false);
  });
});
