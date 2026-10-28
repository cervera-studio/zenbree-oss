// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, TestIntl } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import type { PendingSend } from '@/lib/campaigns/api';
import { ApprovalQueue } from './approval-queue';

const decide = vi.fn();
const edit = vi.fn();
vi.mock('@/app/(app)/campaigns/actions', () => ({
  decideSendsAction: (...args: unknown[]) => decide(...args),
  editSendAction: (...args: unknown[]) => edit(...args),
  undoRejectAction: vi.fn(),
}));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn() } }));
// By default the API applies every message named.
beforeEach(() => {
  vi.clearAllMocks();
  decide.mockReset().mockImplementation((_c: string, verb: string, ids: string[]) =>
    Promise.resolve({
      success: true,
      data: { [verb === 'approve' ? 'approved' : 'rejected']: ids.length },
    }),
  );
  edit.mockReset().mockResolvedValue({ success: true, data: undefined });
  // Monday 5 October 2026, 10:00 in Rome.
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-10-05T08:00:00Z') });
  // The app's intl provider sets no zone: the send time follows the browser's.
  const resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(function (
    this: Intl.DateTimeFormat,
  ) {
    return { ...resolved.call(this), timeZone: 'Europe/Rome' };
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const doneStatus = (container: HTMLElement) =>
  [...container.querySelectorAll('[role="status"]')].find((n) =>
    n.querySelector('h3'),
  ) as HTMLElement;

const send = (id: string) =>
  ({
    id,
    step: 1,
    channel: 'email',
    recipient: `${id}@example.com`,
    firstName: 'Ada',
    lastName: id,
    companyName: 'Acme',
    subject: 'Ciao',
    body: 'Testo',
    sendAt: null,
    sender: null,
    hold: null,
    senderHold: null,
  }) as PendingSend;

// Keeps one root so a rerender is the same mounted queue the server refreshed.
function mount() {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  const show = (items: PendingSend[]): Promise<void> =>
    act(async () => {
      root.render(
        <TestIntl>
          <ApprovalQueue campaignId="c1" items={items} loadFailed={false} />
        </TestIntl>,
      );
    });
  return { container, show };
}

const phoneBar = (c: HTMLElement) => c.querySelector('[data-phone-bar]') as HTMLElement;

describe('ApprovalQueue phone done state', () => {
  it('celebrates after the last approval and links to the inbox', async () => {
    const [a, b] = [send('a'), send('b')];
    const { container, show } = mount();
    await show([a, b]);
    await click(control(phoneBar(container), 'Approva'));
    await show([b]);
    await click(control(phoneBar(container), 'Approva'));
    await show([]);
    expect(decide).toHaveBeenCalledTimes(2);
    const done = doneStatus(container);
    expect(done.textContent).toContain('Tutto rivisto');
    expect(done.textContent).toContain('2 messaggi partono');
    expect(control(done, 'Vai alle risposte').getAttribute('href')).toBe('/inbox');
  });

  it('counts what the API applied, not the messages named', async () => {
    // Both were decided elsewhere meanwhile: the API changed nothing.
    decide
      .mockResolvedValueOnce({ success: true, data: { approved: 0 } })
      .mockResolvedValueOnce({ success: true, data: { rejected: 0 } });
    const [a, b] = [send('a'), send('b')];
    const { container, show } = mount();
    await show([a, b]);
    await click(control(phoneBar(container), 'Approva'));
    await show([b]);
    await click(control(phoneBar(container), 'Salta'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(container.textContent).toContain('0 approvati · 0 saltati');
    await show([]);
    expect(doneStatus(container).textContent).toContain('Nessun messaggio approvato');
  });

  it('counts arrivals mid-review into the total, not into the reviewed', async () => {
    const queue = ['a', 'b', 'c', 'd'].map(send);
    const { container, show } = mount();
    await show(queue);
    await click(control(phoneBar(container), 'Approva'));
    await show(queue.slice(1));
    await click(control(phoneBar(container), 'Salta'));
    await show([...queue.slice(2), send('e'), send('f')]);
    expect(container.textContent).toContain('2 di 6 rivisti');
    expect(container.textContent).toContain('1 approvato · 1 saltato');
  });

  it('shows no done state when nothing came in', async () => {
    const { container, show } = mount();
    await show([]);
    expect(container.textContent).not.toContain('Tutto rivisto');
  });
});

describe('ApprovalQueue cards', () => {
  const card = (c: HTMLElement) => c.querySelector('ul article') as HTMLElement;

  it('tags a message with its copy in an A/B test, and none outside one (#934)', async () => {
    const { container, show } = mount();
    await show([{ ...send('a'), variant: 'b' }]);
    expect(card(container).textContent).toContain('Ada a Acme Variante B');
    await show([{ ...send('a'), variant: null }]);
    expect(card(container).textContent).not.toContain('Variante');
  });

  it('says when and from which mailbox an email goes', async () => {
    const { container, show } = mount();
    await show([
      {
        ...send('a'),
        sendAt: '2026-10-06T07:00:00Z',
        sender: { address: 'ale@ritmosales.it', name: 'Ale' },
      },
    ]);
    expect(card(container).textContent).toContain('Parte domani alle 9:00 da ale@ritmosales.it');
  });

  it('says why the account of the conversation holds an email, and gives no send time', async () => {
    const { container, show } = mount();
    await show([
      {
        ...send('a'),
        sendAt: null,
        sender: { address: 'ale@ritmosales.it', name: 'Ale' },
        senderHold: 'paused',
      },
    ]);
    expect(card(container).textContent).toContain(
      'La conversazione è su ale@ritmosales.it, che è in pausa.',
    );
    expect(card(container).textContent).not.toContain('Parte');
  });

  it('colours the hold line like the senders page: paused is neutral, throttled is a warning', async () => {
    const { container, show } = mount();
    const line = () =>
      container.querySelector('ul article p.text-small:last-of-type') as HTMLElement;
    const hold = (senderHold: 'paused' | 'throttled') => ({
      ...send('a'),
      sender: { address: 'ale@ritmosales.it', name: 'Ale' },
      senderHold,
    });
    await show([hold('paused')]);
    expect(line().className).toContain('text-soft');
    expect(line().className).not.toContain('text-warning');
    await show([hold('throttled')]);
    expect(line().className).toContain('text-warning');
  });

  it('says working hours for LinkedIn and offers the editor without a subject', async () => {
    const { container, show } = mount();
    await show([
      {
        ...send('a'),
        channel: 'linkedin_connect',
        subject: '',
        body: 'Ciao Ada',
        sendAt: '2026-10-06T07:00:00Z',
        sender: null,
      },
    ]);
    expect(card(container).textContent).toContain('Parte domani in orario lavorativo da LinkedIn');
    await click(control(card(container), 'Modifica'));
    expect(card(container).querySelector('input')).toBeNull();
    expect(card(container).textContent).toContain('8 / 300');
    const note = card(container).querySelector('textarea') as HTMLTextAreaElement;
    expect(note.maxLength).toBe(300);
  });

  it('counts a LinkedIn message against 4000 characters', async () => {
    const { container, show } = mount();
    await show([{ ...send('a'), channel: 'linkedin_message', subject: '', body: 'Ciao' }]);
    await click(control(card(container), 'Modifica'));
    const textarea = card(container).querySelector('textarea') as HTMLTextAreaElement;
    expect(textarea.maxLength).toBe(4000);
    expect(card(container).textContent).toContain('4 / 4.000');
  });
});
