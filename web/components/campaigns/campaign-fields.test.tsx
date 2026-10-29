// @vitest-environment happy-dom
import { act } from 'react';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { Preview } from '@/lib/campaigns/api';
import type { CampaignField, FieldCatalog } from '@/lib/campaigns/fields';
import { blankStep } from '@/lib/campaigns/steps';
import { StepPreview } from './builder-side';
import { CampaignFields } from './campaign-fields';

const city: CampaignField = {
  token: 'city',
  source: { kind: 'column', key: 'city' },
  fallback: null,
};
const gone: CampaignField = {
  token: 'sector',
  source: { kind: 'column', key: 'sector' },
  fallback: 'il vostro settore',
};
const catalog: FieldCatalog = {
  people: 5,
  contactTokens: [],
  columns: [
    {
      token: 'city',
      source: { kind: 'column', key: 'city' },
      label: 'Città',
      ai: false,
      filled: 1,
    },
  ],
  signal: [],
};

function panel(fields: CampaignField[], onChange = vi.fn(), body = '') {
  return render(
    <CampaignFields
      fields={fields}
      steps={[{ ...blankStep(), body }]}
      catalog={catalog}
      editable
      onChange={onChange}
    />,
  );
}

const radio = (c: HTMLElement, value: string) => {
  const el = c.querySelector<HTMLInputElement>(`input[type="radio"][value="${value}"]`);
  if (!el) throw new Error(`no ${value} option`);
  return el;
};

describe('CampaignFields', () => {
  it('names each field by its column, token and fill count, a lost column by its key', async () => {
    const c = await panel([city, gone]);
    expect(c.textContent).toContain('Città{{city}} · 1 di 5 persone');
    expect(c.textContent).toContain('sector{{sector}}');
    const [cityRequired, , , sectorFallback] =
      c.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    expect([cityRequired?.checked, sectorFallback?.checked]).toEqual([true, true]);
    expect(c.querySelector<HTMLInputElement>('input:not([type])')?.value).toBe('il vostro settore');
  });

  it('switching to fallback starts a blank text that blocks the save until written', async () => {
    const onChange = vi.fn();
    const c = await panel([city], onChange);
    await click(radio(c, 'fallback'));
    expect(onChange).toHaveBeenCalledWith([{ ...city, fallback: '' }]);

    const blank = await panel([{ ...city, fallback: '' }]);
    expect(blank.textContent).toContain('Scrivi il testo di riserva, o scegli Obbligatorio.');
    expect(blank.querySelector('[aria-invalid="true"]')).not.toBeNull();
  });

  it('switching back to required drops the fallback', async () => {
    const onChange = vi.fn();
    const c = await panel([gone], onChange);
    await click(radio(c, 'required'));
    expect(onChange).toHaveBeenCalledWith([{ ...gone, fallback: null }]);
  });

  it('a field still in a step cannot be removed; an unused one can', async () => {
    const used = await panel([city], vi.fn(), 'Da {{city}}');
    expect(control(used, 'Togli Città').hasAttribute('disabled')).toBe(true);

    const onChange = vi.fn();
    const c = await panel([city], onChange);
    await click(control(c, 'Togli Città'));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});

const item = (missing: string[]): Preview['items'][number] => ({
  target: {
    firstName: 'Ada',
    lastName: 'Lovelace',
    companyName: 'Acme',
    role: 'CEO',
    email: 'ada@acme.test',
    linkedinUrl: '',
  },
  steps: [
    {
      step: 1,
      channel: 'email',
      subject: '',
      body: '',
      error: missing.length ? 'OUTREACH.COPY_MISSING_FIELD' : null,
      signOff: '',
      footer: '',
    },
  ],
  stepsB: null,
  variant: null,
  missing,
});

describe('StepPreview', () => {
  const preview = (missing: string[]) =>
    render(
      <StepPreview
        items={[item(missing)]}
        step={1}
        person={0}
        onPerson={() => {}}
        updating={false}
        failed={false}
        fieldName={(token) => ({ city: 'Città', sector: 'Settore' })[token] ?? token}
      />,
    );

  it('names the fields a person has no value for, by their labels', async () => {
    const c = await preview(['city', 'sector']);
    expect(c.textContent).toContain(
      'Questa persona non ha un valore per Città e Settore: gli step che li usano non partono.',
    );
    expect(c.textContent).toContain('Non inviato: una variabile non ha valore per questa persona.');
  });

  it('says nothing about fields when the person has every value', async () => {
    const c = await act(() => preview([]));
    expect(c.textContent).not.toContain('Questa persona non ha un valore');
  });
});

describe('StepPreview with an A/B test (#934)', () => {
  const rendered = (subject: string) => ({
    step: 1,
    channel: 'email' as const,
    subject,
    body: `${subject} body`,
    error: null,
    signOff: '',
    footer: '',
  });
  const tested = { ...item([]), steps: [rendered('Copy A')], stepsB: [rendered('Copy B')] };
  const preview = (it: Preview['items'][number], abTest: boolean) =>
    render(
      <StepPreview
        items={[it]}
        abTest={abTest}
        step={1}
        person={0}
        onPerson={() => {}}
        updating={false}
        failed={false}
        fieldName={(token) => token}
      />,
    );

  it('shows copy A, and copy B once picked', async () => {
    const c = await preview(tested, true);
    expect(c.textContent).toContain('Copy A body');
    await act(async () => radio(c, 'b').click());
    expect(c.textContent).toContain('Copy B body');
    expect(c.textContent).not.toContain('Copy A body');
  });

  it('shows copy A for B while no step has copy B, and no switch without a test', async () => {
    const c = await preview({ ...tested, stepsB: null }, true);
    await act(async () => radio(c, 'b').click());
    expect(c.textContent).toContain('Copy A body');
    const plain = await preview(tested, false);
    expect(plain.querySelector('input[type="radio"]')).toBeNull();
    expect(plain.textContent).toContain('Copy A body');
  });
});
