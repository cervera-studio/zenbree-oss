// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import { enablePush, localEndpoint, turnOffPush } from '@/lib/notifications/push';
import type { PushConfig } from '@/lib/notifications/push-schema';
import { BrowserCard, useBrowserPush } from './browser-card';

vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/notifications/push', () => ({
  pushSupported: () => true,
  enablePush: vi.fn(),
  localEndpoint: vi.fn(),
  sendTestPush: vi.fn(),
  turnOffPush: vi.fn(),
}));

const ENDPOINT = 'https://fcm.googleapis.com/fcm/send/1';
const browser = { permission: 'default' as NotificationPermission, answer: 'granted' };

function Card(config: PushConfig) {
  return <BrowserCard {...useBrowserPush(config)} />;
}

const text = (c: HTMLElement) => c.textContent ?? '';

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('Notification', {
    get permission() {
      return browser.permission;
    },
  });
  vi.mocked(localEndpoint).mockResolvedValue(null);
  vi.mocked(enablePush).mockImplementation(async (key) => {
    browser.permission = browser.answer as NotificationPermission;
    return browser.permission === 'granted' && key ? ENDPOINT : null;
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('BrowserCard', () => {
  it('without VAPID keys still asks for the permission the in-page alert needs', async () => {
    browser.permission = 'default';
    browser.answer = 'granted';
    const c = await render(<Card publicKey={null} subscribed={[]} />);
    expect(text(c)).toContain('Ricevi un avviso dal browser mentre Zenbree è aperto.');
    await click(control(c, 'Attiva notifiche del browser'));
    expect(enablePush).toHaveBeenCalledWith(null);
    expect(text(c)).toContain('Notifiche del browser attive su questo computer.');
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('stays quiet when the user dismisses the permission prompt', async () => {
    browser.permission = 'default';
    browser.answer = 'default';
    const c = await render(<Card publicKey="BKey" subscribed={[]} />);
    await click(control(c, 'Attiva notifiche del browser'));
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('reports a granted permission whose subscription failed', async () => {
    browser.permission = 'granted';
    vi.mocked(enablePush).mockResolvedValue(null);
    const c = await render(<Card publicKey="BKey" subscribed={[]} />);
    await click(control(c, 'Attiva notifiche del browser'));
    expect(toast.error).toHaveBeenCalledWith(
      'Non siamo riusciti ad attivare le notifiche. Riprova.',
    );
  });

  it('turns push off on this browser from the active card', async () => {
    browser.permission = 'granted';
    vi.mocked(localEndpoint).mockResolvedValue(ENDPOINT);
    const c = await render(<Card publicKey="BKey" subscribed={[ENDPOINT]} />);
    expect(text(c)).toContain('Notifiche del browser attive su questo computer.');
    await click(control(c, 'Disattiva su questo browser'));
    expect(turnOffPush).toHaveBeenCalledOnce();
    expect(text(c)).toContain('Ricevi le risposte anche con Zenbree chiuso.');
  });
});
