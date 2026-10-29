// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vite-plus/test';
import { Avatar } from './avatar';

async function render(image: string | null) {
  const c = document.createElement('div');
  await act(async () =>
    createRoot(c).render(
      <Avatar user={{ name: 'Ada Lovelace', email: 'ada@example.com' }} image={image} />,
    ),
  );
  return c;
}

describe('Avatar', () => {
  it('shows the photo', async () => {
    const c = await render('https://lh3.googleusercontent.com/a');
    expect(c.querySelector('img')?.getAttribute('src')).toBe('https://lh3.googleusercontent.com/a');
  });

  it('falls back to the initials when the photo does not load', async () => {
    const c = await render('https://lh3.googleusercontent.com/a');
    await act(async () => c.querySelector('img')?.dispatchEvent(new Event('error')));
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toBe('AL');
  });

  it('shows the initials with no photo', async () => {
    expect((await render(null)).textContent).toBe('AL');
  });
});
