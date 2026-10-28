// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { usePushPermission } from './push-permission';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function setBrowser(permission: NotificationPermission | null) {
  if (permission === null) {
    vi.stubGlobal('Notification', undefined);
    return;
  }
  vi.stubGlobal('Notification', { permission });
  vi.stubGlobal('PushManager', class {});
  Object.defineProperty(navigator, 'serviceWorker', { value: {}, configurable: true });
}

function Probe({ id }: { id: string }) {
  const { permission, reread } = usePushPermission();
  return (
    <button type="button" id={id} data-permission={permission} onClick={reread}>
      {permission}
    </button>
  );
}

async function mount(...ids: string[]) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () =>
    root.render(
      <div>
        {ids.map((id) => (
          <Probe key={id} id={id} />
        ))}
      </div>,
    ),
  );
  return { host, unmount: () => act(async () => root.unmount()) };
}

const shown = (host: HTMLElement) =>
  [...host.querySelectorAll('button')].map((b) => b.dataset.permission);

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe('usePushPermission', () => {
  it('follows Notification.permission when the window regains focus', async () => {
    setBrowser('default');
    const { host, unmount } = await mount('a');
    expect(shown(host)).toEqual(['default']);
    setBrowser('granted');
    await act(async () => window.dispatchEvent(new Event('focus')));
    expect(shown(host)).toEqual(['granted']);
    await unmount();
  });

  it('reread updates every mounted subscriber', async () => {
    setBrowser('default');
    const { host, unmount } = await mount('a', 'b');
    setBrowser('denied');
    await act(async () => host.querySelector<HTMLButtonElement>('#a')?.click());
    expect(shown(host)).toEqual(['denied', 'denied']);
    await unmount();
  });

  it('stops listening to focus once unmounted', async () => {
    setBrowser('default');
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const { unmount } = await mount('a');
    await unmount();
    const focusAdds = add.mock.calls.filter(([type]) => type === 'focus').length;
    const focusRemoves = remove.mock.calls.filter(([type]) => type === 'focus').length;
    expect(focusRemoves).toBe(focusAdds);
  });

  it('reports unsupported when the browser has no Notification API', async () => {
    setBrowser(null);
    const { host, unmount } = await mount('a');
    expect(shown(host)).toEqual(['unsupported']);
    await unmount();
  });

  it('renders default on the server', () => {
    setBrowser('granted');
    expect(renderToString(<Probe id="a" />)).toContain('default');
  });
});
