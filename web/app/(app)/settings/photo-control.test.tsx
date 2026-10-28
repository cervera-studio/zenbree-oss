// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { api } from '@/lib/api/browser';
import { authClient } from '@/lib/auth-client';
import { PhotoControl } from './photo-control';

const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));
vi.mock('@/lib/api/browser', () => ({ api: vi.fn() }));
vi.mock('@/lib/auth-client', () => ({ authClient: { updateUser: vi.fn() } }));

beforeEach(() => vi.clearAllMocks());

async function choose(c: HTMLElement, file: File) {
  const input = c.querySelector('input[type=file]') as HTMLInputElement;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

describe('PhotoControl', () => {
  it('uploads the file, saves the returned URL on the user and shows it', async () => {
    vi.mocked(api).mockResolvedValue({ success: true, data: { url: 'https://api/x?v=1' } });
    vi.mocked(authClient.updateUser).mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(<PhotoControl name="Ada" email="ada@example.com" image={null} />);
    const file = new File(['x'], 'a.png', { type: 'image/png' });
    await choose(c, file);
    expect(api).toHaveBeenCalledWith('/v1/me/photo', {
      method: 'PUT',
      file: { body: file, type: 'image/png' },
    });
    expect(authClient.updateUser).toHaveBeenCalledWith({ image: 'https://api/x?v=1' });
    expect(c.querySelector('img')?.getAttribute('src')).toBe('https://api/x?v=1');
    expect(refresh).toHaveBeenCalled();
  });

  it('shows the API error by its code and leaves the user unchanged', async () => {
    vi.mocked(api).mockResolvedValue({
      success: false,
      error: { name: 'AppError', code: 'PHOTO.BAD_TYPE', message: 'x', status: 422 },
    });
    const c = await render(<PhotoControl name="Ada" email="ada@example.com" image={null} />);
    await choose(c, new File(['x'], 'a.gif', { type: 'image/gif' }));
    expect(c.querySelector('[role=alert]')?.textContent).toContain('JPEG');
    expect(authClient.updateUser).not.toHaveBeenCalled();
  });

  it('removes the object, then clears user.image', async () => {
    vi.mocked(api).mockResolvedValue({ success: true, data: null });
    vi.mocked(authClient.updateUser).mockResolvedValue({ data: { status: true }, error: null });
    const c = await render(
      <PhotoControl name="Ada" email="ada@example.com" image="https://api/x?v=1" />,
    );
    await click(control(c, 'Rimuovi'));
    expect(api).toHaveBeenCalledWith('/v1/me/photo', { method: 'DELETE' });
    expect(authClient.updateUser).toHaveBeenCalledWith({ image: null });
    expect(c.querySelector('img')).toBeNull();
  });
});
