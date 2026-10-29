// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { RunStep } from '@/lib/automations/api';
import { RunSteps } from './run-steps';

const step = (position: number, overrides: Partial<RunStep>): RunStep => ({
  position,
  kind: 'find_people',
  status: 'succeeded',
  decision: null,
  input: {},
  output: {},
  credits: 0,
  attempts: 1,
  errorCode: null,
  startedAt: '2026-10-16T10:00:00Z',
  finishedAt: '2026-10-16T10:01:00Z',
  ...overrides,
});

async function cards(steps: RunStep[]) {
  const c = await render(<RunSteps steps={steps} />);
  return [...c.querySelectorAll('li')].map((li) => li.textContent);
}

describe('RunSteps', () => {
  it('explains an uncertain step, and the steps after it say the run stopped', async () => {
    const [, emails, campaign] = await cards([
      step(0, { output: { peopleFound: 3 } }),
      step(1, { kind: 'find_emails', status: 'uncertain', decision: 'run_ended' }),
      step(2, { kind: 'add_to_campaign', status: 'skipped', decision: 'run_ended' }),
    ]);
    expect(emails).toContain(
      "L'esecuzione si è fermata durante questo passo, quindi non si sa cosa abbia fatto fuori da Zenbree. La prossima esecuzione può riprendere le stesse persone, ma non le addebita, non le aggiunge alla campagna e non le scrive su HubSpot una seconda volta.",
    );
    expect(emails).not.toContain('Saltato');
    expect(campaign).toContain("Saltato perché l'esecuzione si è fermata prima di questo passo.");
    expect(campaign).not.toContain('fuori da Zenbree');
  });

  it('gives a failed write its error text and says it is not tried again', async () => {
    const [hubspot] = await cards([
      step(4, {
        kind: 'write_hubspot',
        status: 'failed',
        decision: 'failed, not tried again',
        errorCode: 'hubspot_failed',
        output: { existing: 1, created: 0 },
      }),
    ]);
    expect(hubspot).toContain(
      'HubSpot ha rifiutato una scrittura. Le persone scritte prima restano scritte.',
    );
    expect(hubspot).toContain('Non riprovato da solo, perché potrebbe aver già scritto qualcosa.');
    expect(hubspot).toContain('Già su HubSpot1');
  });

  it.each([
    ['vendor_disabled', 'Un fornitore di dati è spento o non collegato.'],
    ['hubspot_off', 'HubSpot non è collegato, o la scrittura è disattivata.'],
    ['campaign_ended', 'La campagna è finita, quindi non si può aggiungere nessuno.'],
  ])('never says a %s failure may have written', async (code, text) => {
    const [failed] = await cards([
      step(4, {
        kind: 'write_hubspot',
        status: 'failed',
        decision: 'failed, not tried again',
        errorCode: code,
      }),
    ]);
    expect(failed).toContain(text);
    expect(failed).not.toContain('potrebbe aver già scritto');
  });

  it('says why a step was skipped and why people were left out of the campaign', async () => {
    const [campaign, hubspot] = await cards([
      step(3, {
        kind: 'add_to_campaign',
        output: {
          enrolled: 1234,
          status: 'draft',
          leftOut: { noContact: 2, stopped: 3, mystery: 5 },
        },
      }),
      step(4, { kind: 'write_hubspot', status: 'skipped', decision: 'no_people_left' }),
    ]);
    expect(campaign).toContain('Aggiunte come bozze1.234');
    expect(campaign).toContain('Nessuna email o LinkedIn utilizzabile2');
    expect(campaign).toContain('Fermate dopo una risposta o una disiscrizione3');
    expect(campaign).not.toContain('mystery');
    expect(hubspot).toContain(
      'Saltato perché dopo i passi precedenti non è rimasta nessuna persona.',
    );
  });

  it('counts the people the Instantly step holds and why it left the others out', async () => {
    const [instantly] = await cards([
      step(3, {
        kind: 'add_to_instantly',
        output: {
          held: 2,
          eligible: 2,
          skipped: { noEmail: 1, alreadyInInstantly: 3, mystery: 4 },
          campaign: { id: 'c1', name: 'Verify running', status: 1, maySend: true },
        },
      }),
    ]);
    expect(instantly).toContain('Persone da aggiungere a Instantly2');
    expect(instantly).toContain('Persone escluse dalla campagna Instantly');
    expect(instantly).toContain('Nessuna email valida1');
    expect(instantly).toContain('Già su Instantly3');
    expect(instantly).not.toContain('mystery');
  });

  it('names why the Instantly step failed', async () => {
    const [instantly] = await cards([
      step(3, {
        kind: 'add_to_instantly',
        status: 'failed',
        errorCode: 'instantly_campaign_not_found',
        output: { instantlyError: 'INSTANTLY.NOT_FOUND' },
      }),
    ]);
    expect(instantly).toContain(
      "La campagna Instantly non esiste più. Scegline un'altra nell'editor.",
    );
  });

  it('says to connect Instantly again when Zenbree cannot read its key', async () => {
    const [instantly] = await cards([
      step(3, {
        kind: 'add_to_instantly',
        status: 'failed',
        errorCode: 'instantly_not_connected',
        output: { instantlyError: 'INTEGRATIONS.CRYPTO_FAILED' },
      }),
    ]);
    expect(instantly).toContain(
      "Non riusciamo a leggere le credenziali salvate. Ricollega l'account.",
    );
    expect(instantly).not.toContain('INTEGRATIONS');
  });

  it('keeps the step text for an Instantly that is not connected', async () => {
    const [instantly] = await cards([
      step(3, {
        kind: 'add_to_instantly',
        status: 'failed',
        errorCode: 'instantly_not_connected',
        output: { instantlyError: 'INSTANTLY.NOT_CONNECTED' },
      }),
    ]);
    expect(instantly).toContain(
      'Instantly non è collegato a questo cliente, oppure la chiave salvata non è leggibile. Ricollegalo da Integrazioni.',
    );
  });
});
