// @vitest-environment happy-dom
import { act, useRef, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { DEFAULT_MODE, type HomeModeId } from '@/lib/chat/home-modes';
import { HomeEmpty } from './home-empty';

vi.mock('@/components/lists/csv-import-dialog', () => ({
  CsvImportDialog: ({ open, draft }: { open: boolean; draft?: string }) =>
    open ? <div data-testid="import" data-draft={draft ?? ''} /> : null,
}));

const send = vi.fn();
beforeEach(() => send.mockClear());

const composer = (c: HTMLElement) => c.querySelector('textarea') as HTMLTextAreaElement;
const imported = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-testid="import"]');

async function type(c: HTMLElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(composer(c), text);
    composer(c).dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function chooseMode(c: HTMLElement, label: string) {
  const trigger = control(c, 'Cosa vuoi fare');
  await act(async () => {
    trigger.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }),
    );
  });
  const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
    (el) => el.textContent === label,
  );
  if (!item) throw new Error(`no mode ${label}`);
  await click(item);
}

const submit = async (c: HTMLElement) => click(control(c, 'Invia'));
function Harness() {
  const [text, setText] = useState('');
  const [mode, setMode] = useState<HomeModeId>(DEFAULT_MODE);
  const composer = useRef<HTMLTextAreaElement>(null);
  return (
    <HomeEmpty
      text={text}
      onText={setText}
      mode={mode}
      onMode={setMode}
      composer={composer}
      onSend={send}
      disabled={false}
    />
  );
}
const open = () => render(<Harness />);

describe('HomeEmpty', () => {
  it('names the composer for a new chat, not an ongoing one', async () => {
    const c = await open();
    expect(composer(c).getAttribute('aria-label')).toBe('Descrivi chi vuoi raggiungere');
  });

  it('sends a find brief without a goal', async () => {
    const c = await open();
    await type(c, 'agenzie a Milano');
    await submit(c);
    expect(send).toHaveBeenCalledWith('agenzie a Milano', { goal: undefined });
  });

  it('sends the campaign goal in find-and-contact mode', async () => {
    const c = await open();
    await chooseMode(c, 'Trova e contatta');
    await type(c, 'CEO di SaaS');
    await submit(c);
    expect(send).toHaveBeenCalledWith('CEO di SaaS', { goal: 'campaign' });
  });

  it('opens the CSV import with the typed text in enrich mode and never sends', async () => {
    const c = await open();
    await chooseMode(c, 'Arricchisci i dati');
    await type(c, 'aggiungi le email');
    await submit(c);
    expect(send).not.toHaveBeenCalled();
    expect(imported(c)?.dataset.draft).toBe('aggiungi le email');
  });

  it('opens the CSV import from the paperclip and the enrich card, with no draft', async () => {
    const c = await open();
    await type(c, 'testo');
    await click(control(c, 'Carica CSV'));
    expect(imported(c)?.dataset.draft).toBe('');

    const d = await open();
    await click(
      control(
        d,
        'Arricchisci i tuoi datiCarica un CSV: trovo email, telefoni e colonne di ricerca AI con la fonte.',
      ),
    );
    expect(imported(d)).not.toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it('sends a chip brief with the selected mode goal, and none in enrich mode', async () => {
    const brief = 'Agenzie di marketing a Milano che assumono SDR';
    const chip = (c: HTMLElement) =>
      [...c.querySelectorAll('button')].find((b) =>
        b.textContent?.startsWith(brief),
      ) as HTMLElement;

    const c = await open();
    await chooseMode(c, 'Trova e contatta');
    await click(chip(c));
    expect(send).toHaveBeenLastCalledWith(brief, { goal: 'campaign' });

    await chooseMode(c, 'Arricchisci i dati');
    await click(chip(c));
    expect(send).toHaveBeenLastCalledWith(brief, { goal: undefined });
  });

  it('selects the mode of a chat card and keeps the composer focused', async () => {
    const c = await open();
    const card = [...c.querySelectorAll('button')].find((b) =>
      b.textContent?.startsWith('Trova e contatta'),
    ) as HTMLElement;
    await click(card);
    expect(composer(c).placeholder).toContain('da contattare');
    expect(document.activeElement).toBe(composer(c));
  });
});
