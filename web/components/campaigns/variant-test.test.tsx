// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { allocationAction } from '@/app/(app)/campaigns/actions';
import { click, control, render } from '@/app/auth/testing';
import { blankStep, type Step } from '@/lib/campaigns/steps';
import { succeed } from '@/lib/types';
import { LiveVariantTest, VariantTest } from './variant-test';

vi.mock('@/app/(app)/campaigns/actions', () => ({ allocationAction: vi.fn() }));

const CAMPAIGN = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const plain: Step[] = [{ ...blankStep(), subject: 'A', body: 'Copy A' }];
const tested: Step[] = [{ ...(plain[0] as Step), b: { subject: 'B', body: 'Copy B' } }];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(allocationAction).mockResolvedValue(succeed({} as never));
});

const draft = (on: boolean, steps: Step[], onSwitch = vi.fn()) =>
  render(
    <VariantTest
      campaign={{ id: CAMPAIGN, bPercent: 50 }}
      on={on}
      steps={steps}
      onSwitch={onSwitch}
    />,
  );
const toggle = (c: HTMLElement) => {
  const el = c.querySelector<HTMLElement>('[role="switch"]');
  if (!el) throw new Error('no switch');
  return click(el);
};

describe('VariantTest in the draft (#934)', () => {
  it('turns the test on, and off at once while no step has copy B', async () => {
    const onSwitch = vi.fn();
    await toggle(await draft(false, plain, onSwitch));
    await toggle(await draft(true, plain, onSwitch));
    expect(onSwitch.mock.calls).toEqual([[true], [false]]);
  });

  it('asks before turning off a test that has copy B', async () => {
    const onSwitch = vi.fn();
    const c = await draft(true, tested, onSwitch);
    await toggle(c);
    expect(onSwitch).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('Disattivare il test A/B?');
    await click(control(document.body, 'Disattiva'));
    expect(onSwitch).toHaveBeenCalledWith(false);
  });

  it('saves the share of copy B as soon as it is picked', async () => {
    const c = await draft(true, tested);
    const select = c.querySelector('select');
    if (!select) throw new Error('no share picker');
    expect([...select.options].map((o) => o.value)).toEqual([
      '10',
      '20',
      '30',
      '40',
      '50',
      '60',
      '70',
      '80',
      '90',
    ]);
    await act(async () => {
      select.value = '30';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(allocationAction).toHaveBeenCalledWith(CAMPAIGN, { bPercent: 30 });
  });
});

describe('LiveVariantTest (#934)', () => {
  const live = (patch: Partial<Parameters<typeof LiveVariantTest>[0]['campaign']>) =>
    render(
      <LiveVariantTest
        campaign={{
          id: CAMPAIGN,
          status: 'active',
          steps: tested,
          bPercent: 30,
          allocationPausedAt: null,
          ...patch,
        }}
      />,
    );

  it('says the share, that joiners keep their copy, and pauses the allocation', async () => {
    const c = await live({});
    expect(c.textContent).toContain('Il 30% delle persone che entrano riceve la variante B.');
    expect(c.textContent).toContain('Chi è già entrato mantiene la sua variante e il suo testo.');
    await click(control(c, 'Sospendi il test'));
    expect(allocationAction).toHaveBeenCalledWith(CAMPAIGN, { paused: true });
  });

  it('says a paused test sends copy A to new people, and resumes it', async () => {
    const c = await live({ allocationPausedAt: '2026-10-20T10:00:00Z' });
    expect(c.textContent).toContain('In pausa: chi entra ora riceve la variante A');
    await click(control(c, 'Riprendi il test'));
    expect(allocationAction).toHaveBeenCalledWith(CAMPAIGN, { paused: false });
  });

  it('shows nothing for a campaign with no test or one that ended', async () => {
    expect((await live({ steps: plain })).textContent).toBe('');
    expect((await live({ status: 'completed' })).textContent).toBe('');
  });
});
