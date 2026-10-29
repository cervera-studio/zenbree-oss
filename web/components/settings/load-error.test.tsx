// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { LoadError } from './load-error';

const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));

describe('load error (#1042)', () => {
  it('says the page did not load and loads it again on retry, without a browser reload', async () => {
    const host = await render(<LoadError message="Impossibile caricare i mittenti." />);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(
      'Impossibile caricare i mittenti.',
    );
    await click(control(host, 'Riprova'));
    expect(refresh).toHaveBeenCalledOnce();
  });
});
