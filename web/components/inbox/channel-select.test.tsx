// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { ChannelSelect } from './channel-select';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
beforeEach(() => push.mockClear());

const options = [
  { value: '', text: 'Tutti i canali', href: '/inbox' },
  { value: 'email', text: 'Email', href: '/inbox?channel=email' },
  { value: 'linkedin', text: 'LinkedIn', href: '/inbox?channel=linkedin' },
];

async function fire(select: HTMLSelectElement, event: Event, value?: string) {
  await act(async () => {
    if (value !== undefined) select.value = value;
    select.dispatchEvent(event);
  });
}

describe('ChannelSelect', () => {
  it('goes to a channel picked from the list', async () => {
    const c = await render(<ChannelSelect label="Canale" value="" options={options} />);
    const select = c.querySelector('select') as HTMLSelectElement;
    await fire(select, new PointerEvent('pointerdown', { bubbles: true }));
    await fire(select, new Event('change', { bubbles: true }), 'email');
    expect(push).toHaveBeenCalledWith('/inbox?channel=email');
  });

  it('waits for Enter when the arrow keys step through a closed select', async () => {
    const c = await render(<ChannelSelect label="Canale" value="" options={options} />);
    const select = c.querySelector('select') as HTMLSelectElement;
    await fire(select, new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await fire(select, new Event('change', { bubbles: true }), 'email');
    await fire(select, new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await fire(select, new Event('change', { bubbles: true }), 'linkedin');
    expect(push).not.toHaveBeenCalled();
    await fire(select, new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(push).toHaveBeenCalledExactlyOnceWith('/inbox?channel=linkedin');
  });

  it('goes on blur after a keyboard change, and nowhere when nothing changed', async () => {
    const c = await render(<ChannelSelect label="Canale" value="email" options={options} />);
    const select = c.querySelector('select') as HTMLSelectElement;
    await fire(select, new FocusEvent('focusout', { bubbles: true }));
    expect(push).not.toHaveBeenCalled();
    await fire(select, new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    await fire(select, new Event('change', { bubbles: true }), '');
    await fire(select, new FocusEvent('focusout', { bubbles: true }));
    expect(push).toHaveBeenCalledExactlyOnceWith('/inbox');
  });
});
