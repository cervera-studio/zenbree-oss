// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { AutomationRunDetail } from '@/lib/automations/api';
import { RunInstantly } from './run-instantly';

type Held = NonNullable<AutomationRunDetail['instantly']>;
const held = (overrides: Partial<Held> = {}): Held => ({
  people: 2,
  batchId: null,
  waitingFor: 'approval',
  campaign: { id: 'c1', name: 'Verify running', status: 1, maySend: true },
  ...overrides,
});

describe('RunInstantly', () => {
  it('names the campaign and why the held people wait', async () => {
    const c = await render(<RunInstantly instantly={held()} batch={null} />);
    expect(c.textContent).toContain('Verify running (In corso)');
    expect(c.textContent).toContain('Persone da aggiungere a Instantly2');
    expect(c.textContent).toContain(
      'Nessuno viene aggiunto a Instantly finché non approvi questa esecuzione.',
    );
  });

  it('shows what the batch did for each outcome, with the campaign link', async () => {
    const c = await render(
      <RunInstantly
        instantly={held({ batchId: 'b1', waitingFor: null, people: 3 })}
        batch={{
          id: 'b1',
          status: 'completed',
          errorCode: null,
          campaign: {
            id: 'c1',
            name: 'Verify running',
            url: 'https://app.instantly.ai/app/campaign/c1',
          },
          counts: { pending: 0, publishing: 0, accepted: 2, skipped: 1, failed: 0, uncertain: 0 },
        }}
      />,
    );
    expect(c.textContent).toContain('Persone aggiunte a Instantly3');
    expect(c.textContent).toContain('Aggiunta a «Verify running» completata');
    expect([...c.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'Aggiunte: 2',
      'Escluse: 1',
    ]);
    const link = c.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://app.instantly.ai/app/campaign/c1');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(c.textContent).not.toContain('Nessuno va a Instantly');
  });

  it('says so when the batch cannot be read', async () => {
    const c = await render(
      <RunInstantly instantly={held({ batchId: 'b1', waitingFor: null })} batch="unreadable" />,
    );
    expect(c.textContent).toContain(
      'Impossibile caricare lo stato delle persone su Instantly. Ricarica la pagina per riprovare.',
    );
  });
});
