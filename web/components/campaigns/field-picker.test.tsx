// @vitest-environment happy-dom
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { CatalogEntry, FieldCatalog } from '@/lib/campaigns/fields';
import { blankStep } from '@/lib/campaigns/steps';
import { FieldPicker } from './field-picker';
import { StepCard } from './step-card';

vi.mock('@/components/ui/dropdown-menu', () => {
  const Inline = ({ children }: { children?: ReactNode }) => <>{children}</>;
  return {
    DropdownMenu: Inline,
    DropdownMenuTrigger: Inline,
    DropdownMenuContent: Inline,
    DropdownMenuItem: ({
      children,
      onSelect,
      'aria-label': name,
    }: {
      children?: ReactNode;
      onSelect: () => void;
      'aria-label': string;
    }) => (
      <button type="button" role="menuitem" aria-label={name} onClick={onSelect}>
        {children}
      </button>
    ),
    DropdownMenuSeparator: () => null,
  };
});

const column = (key: string, label: string, filled: number, ai = false): CatalogEntry => ({
  token: key,
  source: { kind: 'column', key },
  label,
  ai,
  filled,
});
const catalog: FieldCatalog = {
  people: 5,
  contactTokens: ['firstName', 'lastName', 'companyName', 'role'],
  columns: [column('city', 'Città', 4), column('opener', 'Apertura', 1, true)],
  signal: [
    {
      token: 'signalTitle',
      source: { kind: 'signal', key: 'title' },
      label: null,
      ai: false,
      filled: 5,
    },
  ],
};
const items = (c: HTMLElement) =>
  [...c.querySelectorAll('[role="menuitem"]')].map((el) => el.getAttribute('aria-label'));

describe('FieldPicker', () => {
  it('lists the list columns, then the signal, each with how many people have a value', async () => {
    const c = await render(<FieldPicker catalog={catalog} onPick={() => {}} />);
    expect(c.textContent).toContain('Colonne della lista');
    expect(items(c)).toEqual([
      'Città · 4 di 5 persone',
      'Apertura · AI · 1 di 5 persone',
      'Titolo del segnale · 5 di 5 persone',
    ]);
  });

  it('says the list has no columns and leaves the signal out when nobody has one', async () => {
    const c = await render(
      <FieldPicker catalog={{ ...catalog, columns: [], signal: [] }} onPick={() => {}} />,
    );
    expect(c.textContent).toContain('La lista non ha colonne da aggiungere.');
    expect(c.textContent).not.toContain('Segnale');
  });

  it('offers an AI snippet only when the campaign has a list to write it on', async () => {
    const onAddSnippet = vi.fn();
    const without = await render(<FieldPicker catalog={catalog} onPick={() => {}} />);
    expect(without.textContent).not.toContain('Aggiungi uno snippet AI');
    const c = await render(
      <FieldPicker catalog={catalog} onPick={() => {}} onAddSnippet={onAddSnippet} />,
    );
    await click(control(c, 'Aggiungi uno snippet AI'));
    expect(onAddSnippet).toHaveBeenCalled();
  });

  it('gives the picked entry', async () => {
    const onPick = vi.fn();
    const c = await render(<FieldPicker catalog={catalog} onPick={onPick} />);
    await click(control(c, 'Città · 4 di 5 persone'));
    expect(onPick).toHaveBeenCalledWith(catalog.columns[0]);
  });
});

describe('the step editor tokens', () => {
  const step = { ...blankStep(), body: 'Ciao ' };
  const card = (
    onChange: (s: typeof step) => void,
    picker?: Parameters<typeof StepCard>[0]['picker'],
  ) =>
    render(
      <StepCard
        step={step}
        number={1}
        editable
        testing={false}
        onChange={onChange}
        onRemove={() => {}}
        chips={[
          { token: 'firstName', label: 'Nome' },
          { token: 'city', label: 'Città' },
        ]}
        picker={picker}
      />,
    );

  it('writes a chip token at the cursor, the campaign field like a contact token', async () => {
    const onChange = vi.fn();
    const c = await card(onChange);
    await click(control(c, 'Inserisci Città'));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ body: 'Ciao {{city}}' }));
  });

  it('writes the token the picker gives for a new field', async () => {
    const onChange = vi.fn();
    const c = await card(onChange, (insert) => (
      <FieldPicker catalog={catalog} onPick={() => insert('{{opener}}')} />
    ));
    await click(control(c, 'Apertura · AI · 1 di 5 persone'));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ body: 'Ciao {{opener}}' }));
  });
});
