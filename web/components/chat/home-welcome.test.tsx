// @vitest-environment happy-dom
import { act, useRef, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { LOCALES } from '@/i18n/config';
import { MESSAGES } from '@/messages';
import { HomeWelcome, type WelcomeView } from './home-welcome';

const dismissAction = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock('@/app/(app)/actions', () => ({ dismissWelcomeAction: dismissAction }));
vi.mock('@/components/ui/sonner', () => ({ toast: { error: toastError } }));

const send = vi.fn();
beforeEach(() => {
  send.mockReset();
  dismissAction.mockReset();
  toastError.mockReset();
});

const segment: WelcomeView['segments'][number] = {
  key: '0123456789abcdef',
  name: 'Agenzie di marketing',
  titles: ['CEO', 'Resp. commerciale'],
  employeeRange: '10–50',
  message: 'Crea la mia prima lista di contatti per il segmento "Agenzie di marketing".',
  locale: 'it',
};

const base: WelcomeView = {
  firstName: 'Ale',
  companyName: 'Ritmo Sales',
  knowsFrom: 'website',
  segments: [segment],
  steps: [
    { kind: 'profile', done: true },
    { kind: 'list', done: false },
    { kind: 'mailbox', done: false },
  ],
};

function Harness({ welcome }: { welcome: WelcomeView }) {
  const [text, setText] = useState('');
  const composer = useRef<HTMLTextAreaElement>(null);
  return (
    <HomeWelcome
      welcome={welcome}
      text={text}
      onText={setText}
      composer={composer}
      onSend={send}
      disabled={false}
    />
  );
}
const open = (welcome: WelcomeView = base) => render(<Harness welcome={welcome} />);

describe('HomeWelcome', () => {
  it('shows the card line, names the chat after the segment and hides the brief', async () => {
    const c = await open();
    await click(control(c, 'Agenzie di marketingCEO, Resp. commerciale · 10–50'));
    expect(send).toHaveBeenCalledWith(segment.message, {
      goal: undefined,
      segment: '0123456789abcdef',
      title: 'Agenzie di marketing',
    });
  });

  it('cuts a long segment name like the chat title the API saves', async () => {
    const name = `Agenzie di marketing e comunicazione ${'con sede in Lombardia '.repeat(3)}`;
    const c = await open({ ...base, segments: [{ ...segment, name }] });
    await click(control(c, `${name}CEO, Resp. commerciale · 10–50`));
    expect(send.mock.calls[0]?.[1]?.title).toBe(
      'Agenzie di marketing e comunicazione con sede in Lombardia con sede in…',
    );
  });

  it('says when the segments were written in another language than the one shown', async () => {
    const note = "Questi segmenti sono scritti in un'altra lingua.";
    expect((await open()).textContent).not.toContain(note);
    const english = { ...segment, name: 'Marketing agencies', locale: 'en' as const };
    expect((await open({ ...base, segments: [english] })).textContent).toContain(note);
  });

  it('shows no segment cards when there are none', async () => {
    const c = await open({ ...base, segments: [] });
    expect(c.textContent).not.toContain('Dai tuoi segmenti');
  });

  it('sends the typed brief from the composer', async () => {
    const c = await open();
    const area = c.querySelector('textarea') as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(area, 'saas a Torino');
      area.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(control(c, 'Invia'));
    expect(send).toHaveBeenCalledWith('saas a Torino', { goal: undefined });
  });

  it('offers no mode picker or attach on the welcome composer', async () => {
    const c = await open();
    expect(() => control(c, 'Cosa vuoi fare')).toThrow();
    expect(() => control(c, 'Carica CSV')).toThrow();
  });

  it('counts the done steps and greets by first name, or without one', async () => {
    const c = await open();
    expect(c.textContent).toContain('1 di 3');
    expect(c.textContent).toContain('Benvenuto, Ale');
    const nameless = await open({ ...base, firstName: null });
    expect(nameless.textContent).toContain('Benvenuto');
    expect(nameless.textContent).not.toContain('Benvenuto,');
  });

  it('names the company only when it is known', async () => {
    expect((await open()).textContent).toContain('Conosco già Ritmo Sales dal tuo sito');
    const c = await open({ ...base, companyName: null, knowsFrom: 'none' });
    expect(c.textContent).not.toContain('Conosco già');
  });

  it('links each step to the place that completes it', async () => {
    const c = await open();
    expect(control(c, 'Inizia')).toBeTruthy();
    expect(control(c, 'Vedi nel Cliente').getAttribute('href')).toBe('/settings/client');
    expect(control(c, 'Collega').getAttribute('href')).toBe('/settings/integrations');
  });

  it('dismisses once and stays disabled while the call is pending', async () => {
    let finish: (v: { success: true; data: undefined }) => void = () => {};
    dismissAction.mockReturnValue(new Promise((r) => (finish = r)));
    const c = await open();
    const hide = control(c, 'Nascondi');
    await click(hide);
    await click(hide);
    expect(dismissAction).toHaveBeenCalledTimes(1);
    expect((hide as HTMLButtonElement).disabled).toBe(true);
    await act(async () => finish({ success: true, data: undefined }));
    expect(toastError).not.toHaveBeenCalled();
  });

  it('tells the user when dismissing fails', async () => {
    dismissAction.mockResolvedValue({ success: false, error: { code: 'X', message: 'x' } });
    const c = await open();
    await click(control(c, 'Nascondi'));
    expect(toastError).toHaveBeenCalledTimes(1);
  });
});

describe('welcome copy', () => {
  it('never states a credit amount', () => {
    for (const l of LOCALES) {
      expect(JSON.stringify(MESSAGES[l].chat.welcome)).not.toMatch(
        /\d+\s*(crediti|credits?|créditos?|crédits?)/i,
      );
    }
  });
});
