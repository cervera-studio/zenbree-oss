// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import { enablePush } from '@/lib/notifications/push';
import { PushPrompt } from './push-prompt';

vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/notifications/push', () => ({ pushSupported: () => true, enablePush: vi.fn() }));

let permission: NotificationPermission = 'default';

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  permission = 'default';
  vi.stubGlobal('Notification', {
    get permission() {
      return permission;
    },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('PushPrompt', () => {
  it('stays quiet when the user dismisses the permission prompt', async () => {
    vi.mocked(enablePush).mockResolvedValue(null);
    const c = await render(<PushPrompt publicKey="BKey" />);
    await click(control(c, 'Attiva gli avvisi'));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('reports a granted permission whose subscription failed', async () => {
    vi.mocked(enablePush).mockImplementation(async () => {
      permission = 'granted';
      return null;
    });
    const c = await render(<PushPrompt publicKey="BKey" />);
    await click(control(c, 'Attiva gli avvisi'));
    expect(toast.error).toHaveBeenCalledOnce();
  });
});
