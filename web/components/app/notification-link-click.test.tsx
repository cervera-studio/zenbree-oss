// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { NotificationLink } from './notification-link';

const href = '/switch?org=o&next=%2Finbox';
const assign = vi.fn();
beforeEach(() => {
  assign.mockClear();
  Object.defineProperty(window, 'location', { value: { assign }, configurable: true });
});

async function click(onClick: () => unknown, init: MouseEventInit = {}) {
  const c = await render(
    <NotificationLink href={href} onClick={onClick}>
      x
    </NotificationLink>,
  );
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init });
  await act(async () => {
    c.querySelector('a')?.dispatchEvent(event);
  });
  return event;
}

describe('NotificationLink click', () => {
  it.each(['resolves', 'rejects'])('navigates after the mark-read %s', async (kind) => {
    let settle: () => void = () => {};
    const pending = new Promise<void>((resolve, reject) => {
      settle = kind === 'resolves' ? resolve : () => reject(new Error('x'));
    });
    const event = await click(() => pending);
    expect(event.defaultPrevented).toBe(true);
    expect(assign).not.toHaveBeenCalled();
    await act(async () => {
      settle();
      await pending.catch(() => undefined);
    });
    expect(assign).toHaveBeenCalledWith(href);
  });

  it('navigates once after 1.5 s when the mark-read never settles', async () => {
    vi.useFakeTimers();
    try {
      await click(() => new Promise(() => {}));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1499);
      });
      expect(assign).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(assign).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it('leaves a modifier click and a click with nothing pending to the browser', async () => {
    expect((await click(() => Promise.resolve(), { ctrlKey: true })).defaultPrevented).toBe(false);
    expect((await click(() => undefined)).defaultPrevented).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});
