// @vitest-environment happy-dom
import { act, useState } from 'react';
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { Locale } from '@/i18n/config';
import type { Entity, RuleGroup, RuleNode } from '@/lib/segments/rule';
import { MESSAGES } from '@/messages';
import { RuleEditor } from './rule-editor';

const rule = (...children: RuleNode[]): RuleGroup => ({ type: 'group', op: 'and', children });
const employees = { kind: 'canonical', key: 'employees' } as const;
const title = { kind: 'canonical', key: 'title' } as const;

async function editor(initial: RuleGroup, entity: Entity = 'company', locale: Locale = 'it') {
  const now = { rule: initial };
  function Harness() {
    const [current, setCurrent] = useState(initial);
    now.rule = current;
    return (
      <RuleEditor
        rule={current}
        catalog={{ entity, lists: [], columns: {} }}
        marked={null}
        edit={(change) => setCurrent((r) => change(r))}
      />
    );
  }
  const c = await render(<Harness />, locale);
  const label = MESSAGES[locale].segments.editor;
  const all = (name: string) => [
    ...c.querySelectorAll<HTMLInputElement & HTMLSelectElement>(`[aria-label="${name}"]`),
  ];
  const one = (name: string, i = 0) => {
    const el = all(name)[i];
    if (!el) throw new Error(`no control ${name} #${i}`);
    return el;
  };
  const type = (el: HTMLInputElement, value: string) =>
    act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  const choose = (el: HTMLSelectElement, value: string) =>
    act(async () => {
      el.value = value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  const press = (el: HTMLElement, key: string) =>
    act(async () => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    });
  const click = (el: HTMLElement) => act(async () => el.click());
  const invalid = (el: HTMLElement) => el.getAttribute('aria-invalid') === 'true';
  return { c, now, label, all, one, type, choose, press, click, invalid };
}

describe('a bound box shows only text the rule holds', () => {
  it('a removed row leaves no typed text in the row that takes its place', async () => {
    const e = await editor(rule({ type: 'fit', op: 'gte', low: 80 }, { type: 'fit', op: 'gte' }));
    await e.type(e.one('Da', 0), '200');
    expect(e.invalid(e.one('Da', 0))).toBe(true);
    await e.click(e.one('Rimuovi condizione', 0));
    expect(e.all('Da')).toHaveLength(1);
    expect(e.one('Da').value).toBe('');
    expect(e.invalid(e.one('Da'))).toBe(false);
    expect(e.now.rule.children).toEqual([{ type: 'fit', op: 'gte' }]);
  });

  it('a new field empties the boxes along with the bounds', async () => {
    const e = await editor(rule({ type: 'fit', op: 'gte' }));
    await e.type(e.one('Da'), '50');
    expect(e.now.rule.children[0]).toEqual({ type: 'fit', op: 'gte', low: 50 });
    await e.choose(e.one('Campo'), 'canonical:employees');
    expect(e.now.rule.children[0]).toEqual({ type: 'number', field: employees, op: 'gte' });
    expect(e.one('Da').value).toBe('');
  });

  it('holds a pair only when both bounds fit together, and both once they do', async () => {
    const e = await editor(rule({ type: 'fit', op: 'between' }));
    await e.type(e.one('Da'), '60');
    await e.type(e.one('A'), '40');
    expect(e.now.rule.children[0]).toEqual({ type: 'fit', op: 'between' });
    expect([e.invalid(e.one('Da')), e.invalid(e.one('A'))]).toEqual([true, true]);
    await e.type(e.one('Da'), '30');
    expect(e.now.rule.children[0]).toEqual({ type: 'fit', op: 'between', low: 30, high: 40 });
    expect([e.invalid(e.one('Da')), e.invalid(e.one('A'))]).toEqual([false, false]);
    expect([e.one('Da').value, e.one('A').value]).toEqual(['30', '40']);
  });

  it('a bound the operator dropped stays empty when the operator comes back', async () => {
    const e = await editor(rule({ type: 'fit', op: 'gte' }));
    await e.type(e.one('Da'), '50');
    await e.choose(e.one('Confronto'), 'lte');
    expect(e.now.rule.children[0]).toEqual({ type: 'fit', op: 'lte' });
    await e.choose(e.one('Confronto'), 'gte');
    expect(e.now.rule.children[0]).toEqual({ type: 'fit', op: 'gte' });
    expect(e.one('Da').value).toBe('');
  });
});

describe('a change from the keyboard keeps focus on the control', () => {
  it.each([
    ['Confronto', 'lte'],
    ['Campo', 'canonical:employees'],
  ])('%s keeps focus after a change', async (name, value) => {
    const e = await editor(rule({ type: 'fit', op: 'gte', low: 50 }));
    e.one(name).focus();
    await e.choose(e.one(name), value);
    expect(e.one(name).value).toBe(value);
    expect(document.activeElement).toBe(e.one(name));
  });
});

describe('a bound is read in the user language', () => {
  it.each([
    ['it', '1.000', 1000],
    ['it', '1.000,5', 1000.5],
    ['it', '12,5', 12.5],
    ['en', '1,000', 1000],
    ['en', '1.000', 1],
  ] as const)('%s reads %s as %d', async (locale, typed, held) => {
    const e = await editor(
      rule({ type: 'number', field: employees, op: 'gte' }),
      'company',
      locale,
    );
    await e.type(e.one(e.label.low), typed);
    expect(e.now.rule.children[0]).toEqual({
      type: 'number',
      field: employees,
      op: 'gte',
      low: held,
    });
    expect(e.invalid(e.one(e.label.low))).toBe(false);
  });

  it.each([
    ['en', '12,5'],
    ['en', '1.000,5'],
    ['it', '1.5'],
  ] as const)('%s refuses %s and marks it', async (locale, typed) => {
    const e = await editor(
      rule({ type: 'number', field: employees, op: 'gte' }),
      'company',
      locale,
    );
    await e.type(e.one(e.label.low), typed);
    expect(e.now.rule.children[0]).toEqual({ type: 'number', field: employees, op: 'gte' });
    expect(e.one(e.label.low).value).toBe(typed);
    expect(e.invalid(e.one(e.label.low))).toBe(true);
  });

  it.each([
    ['it', '80,5'],
    ['en', '80.5'],
  ] as const)('%s shows a saved bound as %s', async (locale, shown) => {
    const e = await editor(rule({ type: 'fit', op: 'gte', low: 80.5 }), 'company', locale);
    expect(e.one(e.label.low).value).toBe(shown);
  });
});

describe('the values of a list condition', () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => `v${i}`);

  it('keeps a paste past the limit in the box and says why', async () => {
    const values = many(49);
    const e = await editor(rule({ type: 'text', field: title, op: 'in', values }), 'person');
    const box = e.one('Aggiungi un valore');
    await e.type(box, 'a, b');
    await e.press(box, 'Enter');
    expect(e.now.rule.children[0]).toEqual({ type: 'text', field: title, op: 'in', values });
    expect(box.value).toBe('a, b');
    expect(e.c.querySelector('[role="alert"]')?.textContent).toBe(
      'Valori non aggiunti: una condizione accetta al massimo 50 valori, ognuno lungo al massimo 200 caratteri.',
    );
  });

  it('keeps a value that is too long in the box', async () => {
    const e = await editor(rule({ type: 'text', field: title, op: 'in', values: [] }), 'person');
    const box = e.one('Aggiungi un valore');
    await e.type(box, 'x'.repeat(201));
    await e.press(box, 'Enter');
    expect(e.now.rule.children[0]).toEqual({ type: 'text', field: title, op: 'in', values: [] });
    expect(box.value).toBe('x'.repeat(201));
  });

  it('checks each comma part on its own, not the whole paste', async () => {
    const e = await editor(rule({ type: 'text', field: title, op: 'in', values: [] }), 'person');
    const box = e.one('Aggiungi un valore');
    expect(box.hasAttribute('maxlength')).toBe(false);
    await e.type(box, `${'a'.repeat(150)}, ${'b'.repeat(150)}`);
    await e.press(box, 'Enter');
    expect(e.now.rule.children[0]).toEqual({
      type: 'text',
      field: title,
      op: 'in',
      values: ['a'.repeat(150), 'b'.repeat(150)],
    });
    expect(box.value).toBe('');
    expect(e.c.querySelector('[role="alert"]')).toBeNull();
  });
});

it('shows a saved operator the field does not offer', async () => {
  const e = await editor(rule({ type: 'number', field: title, op: 'gt', low: 5 }), 'person');
  const op = e.one('Confronto');
  expect(op.value).toBe('gt');
  expect(op.selectedOptions[0]?.textContent).toBe('è più di');
});
