// @vitest-environment happy-dom
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { type AutomationWebhook, makeWebhookSecret } from '@/lib/automations/api';
import { succeed } from '@/lib/types';
import { RunCall } from './run-detail';
import { WebhookSetup } from './webhook-setup';

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/automations/api', () => ({ makeWebhookSecret: vi.fn() }));

const writeText = vi.fn();
beforeEach(() => {
  vi.resetAllMocks();
  writeText.mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
});
afterEach(() => vi.unstubAllGlobals());

const ID = '00000000-0000-4000-8000-0000000000a1';
const URL = 'https://api.test/v1/automation-hooks/00000000-0000-4000-8000-0000000000c1';
const SECRET = 'whsec_c2VjcmV0LW9mLXRoZS10ZXN0';
const hook = (overrides: Partial<AutomationWebhook> = {}): AutomationWebhook => ({
  url: URL,
  rotatedAt: '2026-10-18T10:00:00Z',
  pending: 0,
  waitingForApproval: false,
  ...overrides,
});
const automation = (webhook: AutomationWebhook | null, enabled = true) => ({
  id: ID,
  name: 'Lead in entrata',
  enabled,
  webhook,
});

function dialog(): HTMLElement {
  const el = document.querySelector<HTMLElement>('[role="dialog"]');
  if (!el) throw new Error('no dialog');
  return el;
}

const shownValues = () =>
  [...dialog().querySelectorAll('input')].map((input) => (input as HTMLInputElement).value);

describe('the webhook of an automation', () => {
  it('makes the first secret and shows it once, with the URL, until the dialog closes', async () => {
    vi.mocked(makeWebhookSecret).mockResolvedValue(
      succeed({ url: URL, secret: SECRET, rotatedAt: '2026-10-18T10:00:00Z' }),
    );
    const c = await render(<WebhookSetup automation={automation(null)} manage />);
    expect(c.textContent).toContain('non ha ancora un segreto, quindi non accetta chiamate');

    await click(control(c, 'Crea il segreto'));
    expect(makeWebhookSecret).toHaveBeenCalledWith(ID);
    expect(dialog().textContent).toContain('Zenbree lo mostra solo questa volta');
    expect(shownValues()).toEqual([URL, SECRET]);
    expect(document.activeElement).toBe(dialog().querySelectorAll('input')[1]);

    await click(control(dialog(), 'Copia Segreto di firma'));
    expect(writeText).toHaveBeenCalledWith(SECRET);
    const live = [...dialog().querySelectorAll('[aria-live="polite"]')].map((p) => p.textContent);
    expect(live).toContain('Segreto di firma copiato.');

    await click(control(dialog(), "L'ho copiato"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it('keeps the secret on screen while the dialog closes, never the rotate prompt', async () => {
    // happy-dom runs no CSS, so give the closing dialog an exit animation, as the real one has.
    const real = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation((el, pseudo) => {
      const styles = real(el, pseudo);
      const closing = () =>
        el.getAttribute('role') === 'dialog' && el.getAttribute('data-state') === 'closed';
      return new Proxy(styles, {
        get: (target, key) => {
          if (key === 'animationName') return closing() ? 'exit' : 'none';
          const value = Reflect.get(target, key);
          return typeof value === 'function' ? value.bind(target) : value;
        },
      });
    });
    vi.mocked(makeWebhookSecret).mockResolvedValue(
      succeed({ url: URL, secret: SECRET, rotatedAt: '2026-10-18T11:00:00Z' }),
    );
    const c = await render(<WebhookSetup automation={automation(hook())} manage />);
    await click(control(c, 'Rinnova il segreto'));
    await click(control(dialog(), 'Rinnova il segreto'));

    await click(control(dialog(), "L'ho copiato"));
    const closing = dialog();
    expect(closing.getAttribute('data-state')).toBe('closed');
    expect(closing.textContent).not.toContain('Il vecchio segreto smette subito di funzionare.');
    expect([...closing.querySelectorAll('button')].map((b) => b.textContent)).not.toContain(
      'Rinnova il segreto',
    );
    for (const button of closing.querySelectorAll('button')) await click(button);
    expect(makeWebhookSecret).toHaveBeenCalledTimes(1);

    const end = new Event('animationend', { bubbles: true });
    Object.defineProperty(end, 'animationName', { value: 'exit' });
    await act(async () => closing.dispatchEvent(end));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it('asks before a rotation that the old secret stops at once, then shows the new one', async () => {
    vi.mocked(makeWebhookSecret).mockResolvedValue(
      succeed({ url: URL, secret: SECRET, rotatedAt: '2026-10-18T11:00:00Z' }),
    );
    const c = await render(<WebhookSetup automation={automation(hook())} manage />);
    expect(c.querySelector('input')?.value).toBe(URL);

    await click(control(c, 'Rinnova il segreto'));
    expect(dialog().textContent).toContain('Il vecchio segreto smette subito di funzionare.');
    expect(makeWebhookSecret).not.toHaveBeenCalled();

    await click(control(dialog(), 'Rinnova il segreto'));
    expect(makeWebhookSecret).toHaveBeenCalledWith(ID);
    expect(shownValues()).toEqual([URL, SECRET]);
  });

  it('shows the calls that wait, and that they wait for an approval', async () => {
    const c = await render(
      <WebhookSetup
        automation={automation(hook({ pending: 2, waitingForApproval: true }), false)}
        manage
      />,
    );
    expect(c.textContent).toContain('2 chiamate attendono di partire.');
    expect(c.textContent).toContain(
      "Le chiamate attendono finché non approvi l'esecuzione di revisione di questa versione.",
    );
    expect(c.textContent).toContain("L'automazione è in pausa");
  });

  it('lets a member read the URL and how to call it, but not make a secret', async () => {
    const none = await render(<WebhookSetup automation={automation(null)} manage={false} />);
    expect(none.textContent).toContain("Può crearlo chi l'ha creata, o un amministratore.");
    expect(none.querySelector('button')).toBeNull();

    const made = await render(<WebhookSetup automation={automation(hook())} manage={false} />);
    expect(made.querySelector('input')?.value).toBe(URL);
    expect(made.textContent).toContain('webhook-signature');
    expect([...made.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Copia']);
  });

  it('says on the run page which call started the run, never the email', async () => {
    const c = await render(
      <RunCall
        delivery={{
          id: '00000000-0000-4000-8000-0000000000d1',
          webhookId: 'msg_2b9',
          receivedAt: '2026-10-18T10:00:00Z',
          matchedBy: 'email',
          rowId: null,
        }}
      />,
    );
    const facts = [...c.querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ]);
    expect(facts).toEqual([
      ['webhook-id', 'msg_2b9'],
      ['Ricevuta', '18 ott 2026, 12:00'],
      ['Persona trovata per', 'Email'],
    ]);
    expect(c.textContent).toContain('La riga della persona è stata eliminata dalla lista.');
  });
});
