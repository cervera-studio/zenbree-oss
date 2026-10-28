// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, TestIntl } from '@/app/auth/testing';
import type { PendingSend } from '@/lib/campaigns/api';
import { ApprovalScreen } from './approval-screen';

const decide = vi.fn();
vi.mock('@/app/(app)/campaigns/actions', () => ({
  decideSendsAction: (...args: unknown[]) => decide(...args),
  editSendAction: vi.fn(),
  undoRejectAction: vi.fn(),
}));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn() } }));
beforeEach(() => {
  decide.mockReset().mockImplementation((_c: string, verb: string, ids: string[]) =>
    Promise.resolve({
      success: true,
      data: { [verb === 'approve' ? 'approved' : 'rejected']: ids.length },
    }),
  );
});

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
  }) as PendingSend;

function mount() {
  const container = document.createElement('div');
  document.body.replaceChildren(container);
  const root = createRoot(container);
  const show = (items: PendingSend[]): Promise<void> =>
    act(async () => {
      root.render(
        <TestIntl>
          <ApprovalScreen
            campaign={{ id: 'c1', name: 'Agenzie' }}
            items={items}
            loadFailed={false}
          />
        </TestIntl>,
      );
    });
  return { container, show };
}

const key = (k: string, init: KeyboardEventInit = {}, target: EventTarget = document.body) =>
  act(async () => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }));
  });

describe('ApprovalScreen', () => {
  it('tags the message under review with its copy in an A/B test (#934)', async () => {
    const { container, show } = mount();
    await show([{ ...send('a'), variant: 'a' }]);
    expect(container.querySelector('article')?.textContent).toContain('Variante A');
  });

  it('approves with A and skips with S, one message at a time', async () => {
    const { container, show } = mount();
    await show([send('a'), send('b'), send('c')]);
    expect(container.textContent).toContain('0 di 3 rivisti');
    await key('a');
    expect(decide).toHaveBeenLastCalledWith('c1', 'approve', ['a']);
    // Gone on the key press, before the server refresh.
    expect(container.querySelector('article')?.textContent).toContain('Ada b');
    await key('s');
    expect(decide).toHaveBeenLastCalledWith('c1', 'reject', ['b']);
    expect(container.textContent).toContain('2 di 3 rivisti');
    expect(container.textContent).toContain('1 approvato · 1 saltato');
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe(
      'Messaggio per Ada c',
    );
  });

  it('ignores the letters with a modifier, a held key or while typing', async () => {
    const { container, show } = mount();
    await show([send('a'), send('b')]);
    await key('a', { metaKey: true });
    await key('a', { repeat: true });
    await key('e');
    const subject = container.querySelector('article input') as HTMLInputElement;
    expect(document.activeElement).toBe(subject);
    await key('a', {}, subject);
    expect(decide).not.toHaveBeenCalled();
    // The editor open, a stray A from outside a field does not send either.
    await key('a');
    expect(decide).not.toHaveBeenCalled();
    await click(control(container, 'Annulla'));
    expect(document.activeElement).toBe(control(container, 'Modifica il testo'));
  });

  it('keeps the same buttons across messages, so focus stays on them', async () => {
    const { container, show } = mount();
    await show([send('a'), send('b')]);
    const approve = control(container, 'Approva');
    approve.focus();
    await click(approve);
    expect(control(container, 'Approva')).toBe(approve);
    expect(document.activeElement).toBe(approve);
    expect(approve.getAttribute('aria-keyshortcuts')).toBe('A');
  });

  it('lets no held Enter or second click decide the next message', async () => {
    const { container, show } = mount();
    await show([send('a'), send('b')]);
    const approve = control(container, 'Approva');
    const enter = (repeat: boolean) => {
      const e = new KeyboardEvent('keydown', {
        key: 'Enter',
        repeat,
        bubbles: true,
        cancelable: true,
      });
      approve.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(enter(false)).toBe(false);
    expect(enter(true)).toBe(true);
    await act(async () => {
      approve.dispatchEvent(new MouseEvent('click', { detail: 2, bubbles: true }));
    });
    expect(decide).not.toHaveBeenCalled();
  });

  it('ends on the done state with a way back to the campaign', async () => {
    const { container, show } = mount();
    await show([send('a')]);
    await key('a');
    await show([]);
    const done = [...container.querySelectorAll('[role="status"]')].find((n) =>
      n.textContent?.includes('Tutto rivisto'),
    ) as HTMLElement;
    expect(control(done, 'Torna alla campagna').getAttribute('href')).toBe('/campaigns/c1');
  });

  it('lists a held message apart, with its reason and no approve action', async () => {
    const { container, show } = mount();
    await show([send('a'), { ...send('b'), hold: 'stop_pending' }]);
    expect(container.textContent).toContain('0 di 1 rivisto');
    const held = container.querySelector('section[aria-labelledby]:last-of-type') as HTMLElement;
    expect(held.textContent).toContain('Trattenuti');
    expect(held.textContent).toContain('Ada b');
    expect(held.textContent).toContain('Stop in corso in Instantly');
    expect(held.textContent).toContain('si è disiscritta o il suo indirizzo è rimbalzato');
    expect(held.querySelector('button')).toBeNull();
    await click(control(container, 'Approva'));
    expect(decide).toHaveBeenLastCalledWith('c1', 'approve', ['a']);
  });

  it('says a message the hold took meanwhile was not approved, and lists it as held after the refresh', async () => {
    decide.mockResolvedValueOnce({ success: true, data: { approved: 0, held: 1 } });
    const { container, show } = mount();
    const status = () =>
      [...container.querySelectorAll('[role="status"]')].find((n) =>
        n.textContent?.includes('non è stato approvato'),
      );
    await show([send('a'), send('b')]);
    const live = container.querySelectorAll('[role="status"]').length;
    await click(control(container, 'Approva'));
    await show([{ ...send('a'), hold: 'held_by_instantly' }, send('b')]);
    expect(container.querySelector('article')?.textContent).toContain('Ada b');
    expect(container.querySelectorAll('[role="status"]').length).toBe(live);
    expect(status()?.textContent).toContain('1 messaggio non è stato approvato e resta in attesa');
    expect(
      control(status() as HTMLElement, 'Vedi le persone della campagna').getAttribute('href'),
    ).toBe('/campaigns/c1');
    expect(container.textContent).toContain('Email da Instantly');
  });

  it('stops counting a message the hold took meanwhile as reviewed, before and after the refresh', async () => {
    decide.mockResolvedValueOnce({ success: true, data: { approved: 0, held: 1 } });
    const { container, show } = mount();
    await show([send('a'), send('b')]);
    expect(container.textContent).toContain('0 di 2 rivisti');
    await click(control(container, 'Approva'));
    expect(container.textContent).toContain('0 di 1 rivisto');
    await show([{ ...send('a'), hold: 'held_by_instantly' }, send('b')]);
    expect(container.textContent).toContain('0 di 1 rivisto');
  });
});
