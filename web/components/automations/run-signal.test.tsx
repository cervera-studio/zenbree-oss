// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { AutomationRunDetail } from '@/lib/automations/api';
import { RunSignal } from './run-signal';
import { SignalSetup } from './signal-setup';

const signal: NonNullable<AutomationRunDetail['signal']> = {
  accountId: '00000000-0000-4000-8000-0000000000c1',
  accountName: 'Hiring Srl',
  signals: [
    { type: 'job_posting', title: 'Senior SDR', score: 99.6, occurredAt: '2026-10-20T09:00:00Z' },
    { type: 'job_posting', title: null, score: 50, occurredAt: '2026-09-20T09:00:00Z' },
  ],
};

const none = { ...signal, signals: [] };

const GIVEN_BACK = 'Questa esecuzione non ha usato i suoi segnali e li ha restituiti.';
const GONE = 'I segnali di questa esecuzione non sono più disponibili.';

describe('the signals of a run', () => {
  it('names the company and each signal that started the run, strongest first', async () => {
    const c = await render(<RunSignal signal={signal} summary={{ taken: { people: 4 } }} />);
    expect(c.querySelector('h2')?.textContent).toBe("Segnali che l'hanno avviata");
    expect(c.querySelector('dd')?.textContent).toBe('Hiring Srl');
    expect([...c.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      expect.stringMatching(/^Senior SDR.*Punteggio 100$/),
      expect.stringMatching(/^Annuncio senza titolo.*Punteggio 50$/),
    ]);
    expect(c.textContent).not.toContain('Nessuna persona');
  });

  it.each([
    [
      'account_gone',
      "Nessuna persona presa in carico: l'azienda è stata eliminata o esclusa dalla lista.",
    ],
    [
      'no_new_person',
      "Nessuna persona presa in carico: nella lista non c'è nessuno di questa azienda che l'automazione non abbia già preso in carico.",
    ],
    [
      'all_refused',
      'Nessuna persona presa in carico: tutte le persone di questa azienda sono state escluse prima di spendere crediti.',
    ],
    ['a_later_reason', 'Nessuna persona di questa azienda è stata presa in carico.'],
  ])('says why a run took nobody: %s', async (nobody, text) => {
    const c = await render(
      <RunSignal
        signal={{ ...none, accountName: null }}
        summary={{ taken: { people: 0, nobody } }}
      />,
    );
    expect(c.querySelector('dd')?.textContent).toBe('Azienda eliminata');
    expect(c.textContent).toContain(text);
    expect(c.textContent).not.toContain(GIVEN_BACK);
  });

  it('says that a run gave back the signals it did not use', async () => {
    const c = await render(<RunSignal signal={none} summary={{ signals: { givenBack: true } }} />);
    expect(c.querySelector('ul')).toBeNull();
    expect(c.textContent).toContain(GIVEN_BACK);
    expect(c.textContent).not.toContain("un'altra esecuzione");
  });

  it.each([
    ['ended unknown before its summary said what it took', {}],
    ['took its people', { taken: { people: 2 } }],
    ['gave up on its account', { signals: { givenUp: true, failedRuns: 3 } }],
  ] as const)(
    'never says that a run that %s gave signals back, unless its summary says so',
    async (_, summary) => {
      const c = await render(<RunSignal signal={none} summary={summary} />);
      expect(c.textContent).toContain(GONE);
      expect(c.textContent).not.toContain(GIVEN_BACK);
    },
  );

  it('says when the account gave its signals up after failed runs', async () => {
    const c = await render(
      <RunSignal signal={signal} summary={{ signals: { givenUp: true, failedRuns: 3 } }} />,
    );
    expect(c.textContent).toContain(
      "Dopo 3 esecuzioni fallite di fila, questi segnali contano come usati. L'azienda avvia di nuovo un'esecuzione solo quando nuovi segnali raggiungono il punteggio minimo.",
    );
  });
});

describe('the signal trigger of an automation', () => {
  const trigger = {
    watchlistId: '00000000-0000-4000-8000-000000000014',
    watchlistName: 'Assunzioni',
    minScore: 80,
    since: '2026-10-21T08:00:00Z',
  };

  it('shows the watchlist, the minimum and since when signals count', async () => {
    const c = await render(<SignalSetup signal={trigger} />);
    const facts = [...c.querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ]);
    expect(facts).toEqual([
      ['Watchlist', 'Assunzioni'],
      ['Punteggio minimo dei segnali', '80'],
      ['Conta i segnali dal', expect.stringContaining('2026')],
    ]);
    expect(c.textContent).not.toContain('eliminata');
  });

  it('says when the watchlist was deleted', async () => {
    const c = await render(
      <SignalSetup signal={{ ...trigger, watchlistId: null, watchlistName: null }} />,
    );
    expect(c.querySelector('dd')?.textContent).toBe('Watchlist eliminata');
    expect(c.textContent).toContain(
      "Questa automazione non avvia esecuzioni finché non scegli un'altra watchlist in Modifica.",
    );
  });
});
