// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { FitChip } from './row-toolbar';

describe('FitChip', () => {
  it('names the floor a saved view set, and turns it off', async () => {
    const onChange = vi.fn();
    const c = await render(<FitChip minFit={50} onChange={onChange} />);
    const chip = control(c, 'Fit oltre 50');
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    await click(chip);
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('turns the floor on at 80', async () => {
    const onChange = vi.fn();
    const c = await render(<FitChip minFit={null} onChange={onChange} />);
    const chip = control(c, 'Fit oltre 80');
    expect(chip.getAttribute('aria-pressed')).toBe('false');
    await click(chip);
    expect(onChange).toHaveBeenCalledWith(80);
  });
});
