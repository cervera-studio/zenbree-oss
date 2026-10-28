// @vitest-environment happy-dom
import { act, useState } from 'react';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, render } from '@/app/auth/testing';
import { blankStep, type Step, type Variant } from '@/lib/campaigns/steps';
import { BuilderSteps } from './builder-steps';
import { StepCard } from './step-card';

const first: Step = { ...blankStep(), subject: 'Idea per {{companyName}}', body: 'Ciao A' };

function Harness({
  start,
  abTest,
  onStep,
  onTest = vi.fn(),
}: {
  start: Step;
  abTest: boolean;
  onStep: (s: Step) => void;
  onTest?: (variant: 'a' | 'b') => void;
}) {
  const [step, setStep] = useState(start);
  return (
    <StepCard
      step={step}
      number={1}
      editable
      testing={false}
      abTest={abTest}
      onChange={(next) => {
        setStep(next);
        onStep(next);
      }}
      onRemove={() => {}}
      onTest={onTest}
    />
  );
}

const card = (start: Step, abTest = true, onStep = vi.fn()) =>
  render(<Harness start={start} abTest={abTest} onStep={onStep} />);

// Radix tabs switch on mouse down, not on click.
async function openTab(c: HTMLElement, name: string) {
  const tab = [...c.querySelectorAll<HTMLElement>('[role="tab"]')].find(
    (t) => t.textContent === name,
  );
  if (!tab) throw new Error(`no tab ${name}`);
  await act(async () => {
    tab.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
  });
}

async function type(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('the copy tabs of a step (#934)', () => {
  it('shows one editor and no tabs while the campaign tests nothing', async () => {
    const c = await card(first, false);
    expect(c.querySelector('[role="tab"]')).toBeNull();
    expect(field(c, 'Oggetto').value).toBe('Idea per {{companyName}}');
  });

  it('says that copy B is copy A until it is written, then starts it from copy A', async () => {
    const onStep = vi.fn();
    const c = await card(first, true, onStep);
    await openTab(c, 'Variante B');
    expect(c.textContent).toContain(
      'Per questo step chi è nella variante B riceve lo stesso testo della variante A.',
    );
    await click(control(c, 'Scrivi la variante B'));
    expect(onStep).toHaveBeenLastCalledWith({
      ...first,
      b: { subject: 'Idea per {{companyName}}', body: 'Ciao A' },
    });
  });

  it('writes copy B on its own, leaving copy A as it was', async () => {
    const onStep = vi.fn();
    const c = await card({ ...first, b: { subject: 'Due minuti?', body: 'Ciao B' } }, true, onStep);
    await openTab(c, 'Variante B');
    expect(field(c, 'Oggetto').value).toBe('Due minuti?');
    const body = c.querySelector('textarea');
    if (!body) throw new Error('no body');
    await type(body, 'Ciao B, di nuovo');
    expect(onStep).toHaveBeenLastCalledWith({
      ...first,
      b: { subject: 'Due minuti?', body: 'Ciao B, di nuovo' },
    });
    await click(control(c, 'Usa la variante A per questo step'));
    expect(onStep.mock.lastCall?.[0].b).toBeUndefined();
    expect(c.textContent).toContain('riceve lo stesso testo della variante A');
  });

  it('sends the test email in the copy of the open tab', async () => {
    const onTest = vi.fn();
    const b = { subject: 'Due minuti?', body: 'Ciao B' };
    const c = await render(
      <Harness start={{ ...first, b }} abTest onStep={vi.fn()} onTest={onTest} />,
    );
    await click(control(c, 'Inviami una prova'));
    await openTab(c, 'Variante B');
    await click(control(c, 'Inviami una prova'));
    expect(onTest.mock.calls).toEqual([['a'], ['b']]);
  });
});

// happy-dom leaves `:disabled` off the controls of a disabled fieldset, which a browser disables.
const disabled = (el: Element | null | undefined) =>
  Boolean(el && (el.matches(':disabled') || el.closest('fieldset[disabled]')));

describe('copy B of a saved step (#934)', () => {
  it('opens in the read-only steps dialog, and stays read-only', async () => {
    const b = { subject: 'Due minuti?', body: 'Ciao B' };
    const c = await render(
      <StepCard
        step={{ ...first, b }}
        number={1}
        editable={false}
        testing={false}
        abTest
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    const tabB = [...c.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(
      (t) => t.textContent === 'Variante B',
    );
    expect(tabB && disabled(tabB)).toBe(false);
    await openTab(c, 'Variante B');
    expect(field(c, 'Oggetto').value).toBe('Due minuti?');
    expect(disabled(field(c, 'Oggetto'))).toBe(true);
    expect(disabled(c.querySelector('textarea'))).toBe(true);
  });
});

function Steps({ start, onTest }: { start: Step[]; onTest: (variant: Variant) => void }) {
  const [steps, setSteps] = useState(start);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <BuilderSteps
      steps={steps}
      open={open}
      abTest
      draft
      pending={false}
      chips={[]}
      catalog={null}
      snippetCampaignId={null}
      onToggle={(i) => setOpen(open === i ? null : i)}
      onChange={() => {}}
      onRemove={(i) => {
        setSteps(steps.toSpliced(i, 1));
        setOpen(null);
      }}
      onAdd={() => {}}
      onPick={() => ''}
      onTest={() => onTest}
    />
  );
}

describe('the open copy tab of a removed step', () => {
  it('does not move to the step that takes its place', async () => {
    const onTest = vi.fn();
    const second: Step = { ...blankStep(first), body: 'Ancora A', b: { subject: '', body: 'B2' } };
    const c = await render(
      <Steps start={[{ ...first, b: { subject: 'B1', body: 'B1' } }, second]} onTest={onTest} />,
    );
    const toggle = () => {
      const el = c.querySelector<HTMLElement>('button[aria-expanded]');
      if (!el) throw new Error('no step');
      return click(el);
    };
    await toggle();
    await openTab(c, 'Variante B');
    await click(control(c, 'Rimuovi step'));
    await toggle();
    expect(c.querySelector('textarea')?.value).toBe('Ancora A');
    await click(control(c, 'Inviami una prova'));
    expect(onTest).toHaveBeenLastCalledWith('a');
  });
});
